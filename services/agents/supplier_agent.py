from __future__ import annotations

import os
from typing import Any, Dict

import anthropic

from shared.schemas import SupplierAlert

SUPPORTED_LANGS = {"en", "hi", "or"}
DEGRADATION_THRESHOLD = 0.05  # delta must be strictly > this to trigger the LLM
MODEL = "claude-3-7-sonnet-20250219"

_LANG_NAMES = {"hi": "Hindi", "or": "Odia"}


class AgentUnavailableError(Exception):
    """Raised whenever the LLM call cannot be completed reliably."""
    pass


class UnsupportedLanguageError(Exception):
    """Raised when an unsupported lang code is passed to run_supplier_agent."""
    pass


SUBMIT_ALERT_TOOL = {
    "name": "submit_alert",
    "description": (
        "Submit the severity assessment and draft escalation message for a "
        "supplier's quality degradation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "trend_description": {"type": "string"},
            "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
            "suggested_action": {"type": "string"},
            "draft_escalation_message": {"type": "string"},
        },
        "required": ["trend_description", "severity", "suggested_action", "draft_escalation_message"],
    },
}

SUBMIT_TRANSLATION_TOOL = {
    "name": "submit_translation",
    "description": "Submit the translated text for the given message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "translated_text": {"type": "string"},
        },
        "required": ["translated_text"],
    },
}


def _build_client() -> anthropic.Anthropic:
    """Constructs the Anthropic client. Raises AgentUnavailableError if no API key is set."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AgentUnavailableError("ANTHROPIC_API_KEY is not set.")
    return anthropic.Anthropic(api_key=api_key)


def execute_tool(name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    Real tool dispatcher — wire this up to the actual supplier-history data source.
    Tests monkeypatch this function directly, so keep this exact name/signature.
    """
    if name == "get_supplier_history":
        # TODO: replace with a real call to your supplier history source, e.g.
        # requests.get(f"{LEDGER_URL}/suppliers/{tool_input['supplier_id']}").json()
        raise NotImplementedError("Wire this up to the real supplier history source.")
    raise ValueError(f"Unknown tool: {name}")


def _call_forced_tool(
    *,
    client: Any,
    system: str,
    user_content: str,
    tool: Dict[str, Any],
    model: str,
) -> Dict[str, Any]:
    """
    Calls the model, forcing it to use `tool`, and returns that tool call's input dict.
    Raises AgentUnavailableError on any transport/rate-limit failure, or if the model
    responds without the expected tool_use block.
    """
    try:
        response = client.messages.create(
            model=model,
            max_tokens=800,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.APIError as e:
        raise AgentUnavailableError(f"LLM call failed: {e}") from e
    except Exception as e:  # belt-and-suspenders for any other transport failure
        raise AgentUnavailableError(f"LLM call failed: {e}") from e

    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool["name"]:
            return block.input

    raise AgentUnavailableError("Model did not return the expected tool call.")


def _compute_degradation(reject_rate_3mo: float, reject_rate_6mo: float) -> float:
    """Positive delta means reject rate is rising (degrading)."""
    return reject_rate_3mo - reject_rate_6mo


def _no_degradation_alert(supplier_id: str, reject_rate_3mo: float, reject_rate_6mo: float) -> SupplierAlert:
    return SupplierAlert(
        supplier_id=supplier_id,
        severity="NONE",
        trend_description=(
            f"No significant degradation (3mo={reject_rate_3mo:.1%}, 6mo={reject_rate_6mo:.1%})"
        ),
        suggested_action="No action needed",
        draft_escalation_message="",
    )


def _not_found_alert(supplier_id: str) -> SupplierAlert:
    return SupplierAlert(
        supplier_id=supplier_id,
        severity="NONE",
        trend_description=f"No supplier record found for {supplier_id}",
        suggested_action="Verify supplier_id is correct",
        draft_escalation_message="",
    )


def _translate_message(client: Any, message: str, lang: str) -> str:
    """Translates message into lang, falling back to the original on any failure or empty output."""
    lang_name = _LANG_NAMES.get(lang, lang)
    system = f"Translate the following procurement escalation message into {lang_name}."

    try:
        result = _call_forced_tool(
            client=client,
            system=system,
            user_content=message,
            tool=SUBMIT_TRANSLATION_TOOL,
            model=MODEL,
        )
    except AgentUnavailableError:
        return message

    translated = (result.get("translated_text") or "").strip()
    return translated if translated else message


def run_supplier_agent(supplier_id: str, lang: str = "en") -> SupplierAlert:
    """
    Main entry point. Returns a SupplierAlert with severity "NONE" if the supplier
    is unknown or not degrading (no LLM call made in either case). Otherwise calls
    the LLM for a severity assessment + draft escalation message, translating the
    draft message if lang != "en".
    """
    if lang not in SUPPORTED_LANGS:
        raise UnsupportedLanguageError(f"Unsupported lang: {lang}")

    payload = execute_tool("get_supplier_history", {"supplier_id": supplier_id})

    if not payload or payload.get("error"):
        return _not_found_alert(supplier_id)

    reject_rate_3mo = payload["reject_rate_3mo"]
    reject_rate_6mo = payload["reject_rate_6mo"]
    delta = _compute_degradation(reject_rate_3mo, reject_rate_6mo)

    if delta <= DEGRADATION_THRESHOLD:
        return _no_degradation_alert(supplier_id, reject_rate_3mo, reject_rate_6mo)

    client = _build_client()

    system = (
        f"You are a procurement quality analyst. Supplier {supplier_id}'s reject rate "
        f"rose from {reject_rate_6mo:.1%} (6mo) to {reject_rate_3mo:.1%} (3mo), a delta "
        f"of {delta:.1%}. Assess severity (LOW, MEDIUM, HIGH) and draft an escalation message."
    )
    user_content = (
        f"supplier_id={supplier_id}, reject_rate_3mo={reject_rate_3mo}, "
        f"reject_rate_6mo={reject_rate_6mo}"
    )

    alert_input = _call_forced_tool(
        client=client,
        system=system,
        user_content=user_content,
        tool=SUBMIT_ALERT_TOOL,
        model=MODEL,
    )

    severity = alert_input.get("severity")
    if severity not in ("LOW", "MEDIUM", "HIGH"):
        severity = "MEDIUM"  # default on invalid/unexpected LLM output

    draft_message = alert_input.get("draft_escalation_message", "")
    if lang != "en" and draft_message:
        draft_message = _translate_message(client, draft_message, lang)

    return SupplierAlert(
        supplier_id=supplier_id,
        severity=severity,
        trend_description=alert_input.get("trend_description", f"Reject rate delta: {delta:.1%}"),
        suggested_action=alert_input.get("suggested_action", "Escalate to procurement"),
        draft_escalation_message=draft_message,
    )