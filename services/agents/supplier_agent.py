"""
services/agents/supplier_agent.py

Person 4 (Agentic Layer) — Supplier Intelligence Agent for MediTrust.

Two SIH production-enhancement beats live here:

1. Supplier Trend Threshold Rule Engine:
   Before ever invoking Gemini, this module evaluates the standard
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
   narrowly-scoped Gemini call. The shared `SupplierAlert` schema is
   untouched — `draft_escalation_message` remains a single string field;
   localization simply changes which language is in it.

Error handling mirrors qa_agent.py: Google GenAI failures are caught and
re-raised as `AgentUnavailableError` (imported from qa_agent to keep one
exception type across the whole agent layer), for router.py to map to
HTTP 503.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from google import genai
from google.genai import types
from google.genai.errors import APIError

from shared.schemas import SupplierAlert
from services.agents.tools import execute_tool
from services.agents.qa_agent import AgentUnavailableError

logger = logging.getLogger("meditrust.agents.supplier_agent")

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_PRIMARY_MODEL = "gemini-2.5-flash"
SUPPLIER_AGENT_MODEL = os.environ.get("SUPPLIER_AGENT_MODEL", DEFAULT_PRIMARY_MODEL)

DEGRADATION_THRESHOLD = 0.05

_SUPPORTED_LANGS = {"en": "English", "hi": "Hindi", "or": "Odia"}
_VALID_SEVERITIES = {"NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"}


# --------------------------------------------------------------------------- #
# Structured Response Schemas
# --------------------------------------------------------------------------- #

class AlertAnalysisResponse(BaseModel):
    trend_description: str = Field(
        description="Plain-language description of the degradation trend, citing actual reject rates."
    )
    severity: str = Field(
        description="Severity levels: LOW, MEDIUM, HIGH, or CRITICAL."
    )
    suggested_action: str = Field(
        description="Concrete, specific next action for procurement/QA team."
    )
    draft_escalation_message: str = Field(
        description="Ready-to-send escalation message in English referencing actual figures."
    )


class TranslationResponse(BaseModel):
    translated_text: str = Field(
        description="The escalation message translated in full."
    )


ANALYSIS_SYSTEM_PROMPT = """You are the MediTrust Supplier Intelligence Agent. A programmatic \
rule has already determined that this supplier's 3-month reject rate has degraded more than \
5 percentage points versus their 6-month reject rate — you are being invoked specifically to \
characterize that degradation and draft an escalation, not to re-decide whether it is significant.

Base every statement strictly on the supplier data provided to you in this message. Do not \
invent figures, dates, or incidents that were not given to you. Reference the actual reject \
rate numbers in both trend_description and draft_escalation_message."""

TRANSLATION_SYSTEM_PROMPT_TEMPLATE = """You are a professional {language} translator for a \
hospital supply-chain quality system. Translate the escalation message you are given into \
formal, professional {language}, suitable for a procurement leadership audience. Preserve all \
numbers, percentages, dates, and supplier/batch identifiers exactly as given — do not localize \
or alter them. Do not add commentary, notes, or an English restatement."""


class UnsupportedLanguageError(ValueError):
    """Raised when `lang` is provided but is not one of the supported codes."""


# --------------------------------------------------------------------------- #
# Client Construction & LLM Invocation
# --------------------------------------------------------------------------- #

def _build_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise AgentUnavailableError("GEMINI_API_KEY environment variable is missing.")
    return genai.Client(api_key=api_key)


def _call_structured_gemini(
    client: genai.Client,
    system_instruction: str,
    user_content: str,
    response_schema: type[BaseModel],
    model: str,
) -> Dict[str, Any]:
    """
    Executes a Gemini request with structured output JSON enforcement.
    """
    try:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0.2,
        )
        response = client.models.generate_content(
            model=model,
            contents=user_content,
            config=config,
        )
        if not response.text:
            raise AgentUnavailableError("Received empty response from Gemini API.")
        
        return json.loads(response.text)
    except APIError as exc:
        logger.error("Gemini API error during generation: %s", exc)
        raise AgentUnavailableError(f"Gemini API error: {str(exc)}") from exc
    except Exception as exc:
        logger.error("Unexpected error during Gemini execution: %s", exc)
        raise AgentUnavailableError(f"Failed to generate structured agent output: {str(exc)}") from exc


# --------------------------------------------------------------------------- #
# Threshold Rule Engine Helpers
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
# Public Entry Point
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
    """
    if lang is not None and lang not in _SUPPORTED_LANGS:
        raise UnsupportedLanguageError(
            f"Unsupported lang '{lang}'. Supported values: {sorted(_SUPPORTED_LANGS)}."
        )

    supplier_data = execute_tool("get_supplier_history", {"supplier_id": supplier_id})

    if not supplier_data or "error" in supplier_data:
        error_msg = supplier_data.get("error") if isinstance(supplier_data, dict) else "No data returned"
        logger.info("Supplier lookup failed for '%s': %s", supplier_id, error_msg)
        return _not_found_alert(supplier_id)

    reject_rate_3mo = float(supplier_data["reject_rate_3mo"])
    reject_rate_6mo = float(supplier_data["reject_rate_6mo"])
    delta = _compute_degradation(reject_rate_3mo, reject_rate_6mo)

    # --- Rule engine gate: no LLM call at all unless this trips ---
    if delta <= DEGRADATION_THRESHOLD:
        return _no_degradation_alert(supplier_id, reject_rate_3mo, reject_rate_6mo)

    active_model = model or SUPPLIER_AGENT_MODEL
    client = _build_client()

    analysis_input = _call_structured_gemini(
        client=client,
        system_instruction=ANALYSIS_SYSTEM_PROMPT,
        user_content=(
            "Supplier data (already fetched from the system of record):\n"
            f"{supplier_data}\n\n"
            f"Computed degradation: 3mo reject rate {reject_rate_3mo:.4f} minus 6mo reject rate "
            f"{reject_rate_6mo:.4f} = {delta:+.4f}, which exceeds the "
            f"{DEGRADATION_THRESHOLD:.2f} threshold."
        ),
        response_schema=AlertAnalysisResponse,
        model=active_model,
    )

    severity = str(analysis_input.get("severity", "")).upper()
    if severity not in _VALID_SEVERITIES - {"NONE"}:
        logger.warning("Agent returned invalid severity %r; defaulting to MEDIUM.", severity)
        severity = "MEDIUM"

    draft_escalation_message = str(analysis_input.get("draft_escalation_message", "")).strip()

    # Skip translation call when lang is English or None
    if lang is not None and lang != "en" and draft_escalation_message:
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


def _translate_message(client: genai.Client, text: str, lang: str, model: str) -> str:
    language_name = _SUPPORTED_LANGS[lang]
    translation_input = _call_structured_gemini(
        client=client,
        system_instruction=TRANSLATION_SYSTEM_PROMPT_TEMPLATE.format(language=language_name),
        user_content=text,
        response_schema=TranslationResponse,
        model=model,
    )
    translated = str(translation_input.get("translated_text", "")).strip()
    return translated or text