"""
services/agents/supplier_agent.py

Person 4 (Agentic Layer) — Supplier Intelligence Agent for MediTrust.

Two SIH production-enhancement beats live here:

1. Supplier Trend Threshold Rule Engine:
   Before ever invoking Claude, this module evaluates the standard
   degradation metric programmatically:

        reject_rate_3mo - reject_rate_6mo > 0.05

   If the supplier has NOT degraded past that threshold, the agent returns
   `severity: "NONE"` immediately — no LLM call, no cost, no latency. The
   LLM is only invoked once real degradation is detected, and only to do
   the work a rule can't: characterize the trend in language and draft an
   escalation.

2. Multilingual Alert Engine:
   When a caller passes `lang="hi"` or `lang="or"`, the agent's English
   `draft_escalation_message` is translated into Hindi or Odia via a second,
   narrowly-scoped Claude call. The shared `SupplierAlert` schema is
   untouched — `draft_escalation_message` remains a single string field;
   localization simply changes which language is in it.

Both LLM calls use forced tool_choice against a single-purpose "submit_*"
tool so the result is always structurally valid — never freeform text that
needs post-hoc parsing.

Error handling mirrors qa_agent.py: Anthropic SDK failures are caught and
re-raised as `AgentUnavailableError` (imported from qa_agent to keep one
exception type across the whole agent layer), for router.py to map to
HTTP 503.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import anthropic
from anthropic.types import MessageParam, ToolParam

from shared.schemas import SupplierAlert
from services.agents.tools import execute_tool
from services.agents.qa_agent import AgentUnavailableError

logger = logging.getLogger("meditrust.agents.supplier_agent")

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_PRIMARY_MODEL = "claude-3-7-sonnet-20250219"
SUPPLIER_AGENT_MODEL = os.environ.get("SUPPLIER_AGENT_MODEL", DEFAULT_PRIMARY_MODEL)
MAX_TOKENS = 800

DEGRADATION_THRESHOLD = 0.05

_SUPPORTED_LANGS = {"hi": "Hindi", "or": "Odia"}

_VALID_SEVERITIES = {"NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"}


# --------------------------------------------------------------------------- #
# Forced-output tools
# --------------------------------------------------------------------------- #

SUBMIT_ALERT_TOOL: ToolParam = {
    "name": "submit_alert",
    "description": (
        "Submit your analysis of this supplier's quality degradation, exactly once, as "
        "your final action. Base every claim strictly on the reject-rate figures and "
        "flagged incidents provided — do not invent numbers."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "trend_description": {
                "type": "string",
                "description": "Plain-language description of the degradation trend, citing "
                "the actual 3mo/6mo reject rates and any relevant flagged incidents.",
            },
            "severity": {
                "type": "string",
                "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                "description": "LOW: marginal degradation, monitor. MEDIUM: clear degradation, "
                "warrants review. HIGH: significant degradation, warrants supplier hold. "
                "CRITICAL: severe degradation with incident history, warrants immediate escalation.",
            },
            "suggested_action": {
                "type": "string",
                "description": "A concrete, specific next action for the procurement/QA team.",
            },
            "draft_escalation_message": {
                "type": "string",
                "description": "A ready-to-send escalation message (English) a QA officer could "
                "forward to procurement leadership or the supplier, referencing the actual figures.",
            },
        },
        "required": ["trend_description", "severity", "suggested_action", "draft_escalation_message"],
    },
}

SUBMIT_TRANSLATION_TOOL: ToolParam = {
    "name": "submit_translation",
    "description": "Submit the translated escalation message exactly once, as your final action.",
    "input_schema": {
        "type": "object",
        "properties": {
            "translated_text": {
                "type": "string",
                "description": "The escalation message translated in full, preserving its "
                "meaning, tone, and any figures/dates exactly as given.",
            }
        },
        "required": ["translated_text"],
    },
}


ANALYSIS_SYSTEM_PROMPT = """You are the MediTrust Supplier Intelligence Agent. A programmatic \
rule has already determined that this supplier's 3-month reject rate has degraded more than \
5 percentage points versus their 6-month reject rate — you are being invoked specifically to \
characterize that degradation and draft an escalation, not to re-decide whether it is significant.

Base every statement strictly on the supplier data provided to you in this message. Do not \
invent figures, dates, or incidents that were not given to you. Reference the actual reject \
rate numbers in both trend_description and draft_escalation_message.

When ready, call the submit_alert tool exactly once with your analysis."""

TRANSLATION_SYSTEM_PROMPT_TEMPLATE = """You are a professional {language} translator for a \
hospital supply-chain quality system. Translate the escalation message you are given into \
formal, professional {language}, suitable for a procurement leadership audience. Preserve all \
numbers, percentages, dates, and supplier/batch identifiers exactly as given — do not localize \
or alter them. Do not add commentary, notes, or an English restatement.

When ready, call the submit_translation tool exactly once with the translated text."""


class UnsupportedLanguageError(ValueError):
    """Raised when `lang` is provided but is not one of the supported codes."""


# --------------------------------------------------------------------------- #
# Client construction
# --------------------------------------------------------------------------- #

def _build_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AgentUnavailableError("ANTHROPIC_API_KEY is not configured.")
    return anthropic.Anthropic(api_key=api_key)


def _call_forced_tool(
    client: anthropic.Anthropic,
    system: str,
    user_content: str,
    tool: ToolParam,
    model: str,
) -> Dict[str, Any]:
    """
    Make a single Claude call with tool_choice forced to `tool`, and return
    that tool call's validated input dict. Anthropic SDK errors are caught
    and re-raised as AgentUnavailableError.
    """
    messages: List[MessageParam] = [{"role": "user", "content": user_content}]

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=messages,
        )
    except anthropic.AuthenticationError as exc:
        logger.error("Anthropic authentication failed: %s", exc)
        raise AgentUnavailableError("Authentication with the Anthropic API failed.", exc) from exc
    except anthropic.RateLimitError as exc:
        logger.warning("Anthropic rate limit hit: %s", exc)
        raise AgentUnavailableError("The Anthropic API rate limit was exceeded.", exc) from exc
    except anthropic.APIConnectionError as exc:
        logger.error("Could not connect to the Anthropic API: %s", exc)
        raise AgentUnavailableError("Could not connect to the Anthropic API.", exc) from exc
    except anthropic.APIStatusError as exc:
        logger.error("Anthropic API returned an error status: %s", exc)
        raise AgentUnavailableError(f"Anthropic API error (status {exc.status_code}).", exc) from exc

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool["name"]:
            return block.input or {}

    # Forced tool_choice guarantees a matching tool_use block under normal
    # operation; this is a defensive fallback for a malformed/empty response.
    logger.error("Forced tool call to '%s' did not return a matching tool_use block.", tool["name"])
    raise AgentUnavailableError(f"Anthropic API did not return the expected '{tool['name']}' tool call.")


# --------------------------------------------------------------------------- #
# Threshold rule engine
# --------------------------------------------------------------------------- #

def _compute_degradation(reject_rate_3mo: float, reject_rate_6mo: float) -> float:
    return reject_rate_3mo - reject_rate_6mo


def _no_degradation_alert(supplier_id: str, reject_rate_3mo: float, reject_rate_6mo: float) -> SupplierAlert:
    delta = _compute_degradation(reject_rate_3mo, reject_rate_6mo)
    return SupplierAlert(
        supplier_id=supplier_id,
        trend_description=(
            f"No significant degradation detected. 3-month reject rate is "
            f"{reject_rate_3mo:.2%}, 6-month reject rate is {reject_rate_6mo:.2%} "
            f"(delta {delta:+.2%}, threshold {DEGRADATION_THRESHOLD:.0%})."
        ),
        severity="NONE",
        suggested_action="No action required; continue routine monitoring.",
        draft_escalation_message="",
    )


def _not_found_alert(supplier_id: str) -> SupplierAlert:
    return SupplierAlert(
        supplier_id=supplier_id,
        trend_description=f"No supplier record found for supplier_id '{supplier_id}'.",
        severity="NONE",
        suggested_action="Verify the supplier_id and retry.",
        draft_escalation_message="",
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def run_supplier_agent(
    supplier_id: str,
    lang: Optional[str] = None,
    model: Optional[str] = None,
) -> SupplierAlert:
    """
    Evaluate a supplier's quality trend and, only if a real degradation is
    detected, produce an LLM-authored trend description, severity, suggested
    action, and draft escalation message — optionally localized.

    Args:
        supplier_id: Supplier to evaluate.
        lang: Optional 'hi' (Hindi) or 'or' (Odia). When set and the supplier
            has degraded, draft_escalation_message is returned in that
            language instead of English. Ignored when severity is NONE
            (nothing to translate).
        model: Override the Claude model used for both the analysis and
            translation calls.

    Returns:
        A validated SupplierAlert. severity="NONE" cases never touch the
        LLM at all.

    Raises:
        UnsupportedLanguageError: if `lang` is provided but not 'hi' or 'or'.
        AgentUnavailableError: on Anthropic connectivity/auth/rate-limit
            failures. Callers (router.py) should map this to HTTP 503.
    """
    if lang is not None and lang not in _SUPPORTED_LANGS:
        raise UnsupportedLanguageError(
            f"Unsupported lang '{lang}'. Supported values: {sorted(_SUPPORTED_LANGS)}."
        )

    supplier_data = execute_tool("get_supplier_history", {"supplier_id": supplier_id})

    if "error" in supplier_data:
        logger.info("Supplier lookup failed for '%s': %s", supplier_id, supplier_data["error"])
        return _not_found_alert(supplier_id)

    reject_rate_3mo = float(supplier_data["reject_rate_3mo"])
    reject_rate_6mo = float(supplier_data["reject_rate_6mo"])
    delta = _compute_degradation(reject_rate_3mo, reject_rate_6mo)

    # --- Rule engine gate: no LLM call at all unless this trips. ---
    if delta <= DEGRADATION_THRESHOLD:
        return _no_degradation_alert(supplier_id, reject_rate_3mo, reject_rate_6mo)

    active_model = model or SUPPLIER_AGENT_MODEL
    client = _build_client()

    analysis_input = _call_forced_tool(
        client=client,
        system=ANALYSIS_SYSTEM_PROMPT,
        user_content=(
            "Supplier data (already fetched from the system of record):\n"
            f"{supplier_data}\n\n"
            f"Computed degradation: 3mo reject rate {reject_rate_3mo:.4f} minus 6mo reject rate "
            f"{reject_rate_6mo:.4f} = {delta:+.4f}, which exceeds the "
            f"{DEGRADATION_THRESHOLD:.2f} threshold."
        ),
        tool=SUBMIT_ALERT_TOOL,
        model=active_model,
    )

    severity = str(analysis_input.get("severity", "")).upper()
    if severity not in _VALID_SEVERITIES - {"NONE"}:
        logger.warning("submit_alert returned invalid severity %r; defaulting to MEDIUM.", severity)
        severity = "MEDIUM"

    draft_escalation_message = str(analysis_input.get("draft_escalation_message", "")).strip()

    if lang is not None and draft_escalation_message:
        draft_escalation_message = _translate_message(
            client=client,
            text=draft_escalation_message,
            lang=lang,
            model=active_model,
        )

    return SupplierAlert(
        supplier_id=supplier_id,
        trend_description=str(analysis_input.get("trend_description", "")).strip(),
        severity=severity,
        suggested_action=str(analysis_input.get("suggested_action", "")).strip(),
        draft_escalation_message=draft_escalation_message,
    )


def _translate_message(client: anthropic.Anthropic, text: str, lang: str, model: str) -> str:
    language_name = _SUPPORTED_LANGS[lang]
    translation_input = _call_forced_tool(
        client=client,
        system=TRANSLATION_SYSTEM_PROMPT_TEMPLATE.format(language=language_name),
        user_content=text,
        tool=SUBMIT_TRANSLATION_TOOL,
        model=model,
    )
    translated = str(translation_input.get("translated_text", "")).strip()
    return translated or text