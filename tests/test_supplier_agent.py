"""
tests/test_supplier_agent.py

Unit tests for services/agents/supplier_agent.py.

No real network or Gemini API calls are made: `supplier_agent._build_client`
and `supplier_agent.execute_tool` are monkeypatched with fakes, following the
same pattern as tests/test_qa_agent.py. `client.models.generate_content(...)`
is faked to return an object with a `.text` attribute holding the JSON string
that `_call_structured_gemini` expects to `json.loads(...)` — matching the
google-genai structured-output response shape, not Anthropic tool_use blocks.

Several tests assert that the LLM is NOT invoked at all when the threshold
rule engine short-circuits — that's the core cost/latency guarantee this
module makes.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from google.genai.errors import APIError

from services.agents import supplier_agent
from shared.schemas import SupplierAlert


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #

def _analysis_response(
    trend_description: str,
    severity: str,
    suggested_action: str,
    draft_escalation_message: str,
) -> SimpleNamespace:
    """Mimics the google-genai response for a structured AlertAnalysisResponse call."""
    payload = {
        "trend_description": trend_description,
        "severity": severity,
        "suggested_action": suggested_action,
        "draft_escalation_message": draft_escalation_message,
    }
    return SimpleNamespace(text=json.dumps(payload))


def _translation_response(translated_text: str) -> SimpleNamespace:
    """Mimics the google-genai response for a structured TranslationResponse call."""
    return SimpleNamespace(text=json.dumps({"translated_text": translated_text}))


class _FakeModels:
    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("_FakeModels.generate_content called more times than responses were queued.")
        next_item = self._responses.pop(0)
        if isinstance(next_item, BaseException):
            raise next_item
        return next_item


class _FakeClient:
    def __init__(self, responses: List[Any]) -> None:
        self.models = _FakeModels(responses)


@pytest.fixture(autouse=True)
def _api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")


def _patch_client(monkeypatch: pytest.MonkeyPatch, responses: List[Any]) -> _FakeClient:
    fake_client = _FakeClient(responses)
    monkeypatch.setattr(supplier_agent, "_build_client", lambda: fake_client)
    return fake_client


def _forbid_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch _build_client to fail loudly if the rule engine incorrectly invokes the LLM."""

    def _raise() -> Any:
        raise AssertionError("_build_client should not be called when severity is NONE.")

    monkeypatch.setattr(supplier_agent, "_build_client", _raise)


def _make_api_error(code: int, message: str) -> APIError:
    return APIError(code, {"error": {"message": message, "status": "ERROR"}})


def _supplier_payload(
    supplier_id: str = "SUP-001",
    reject_rate_3mo: float = 0.02,
    reject_rate_6mo: float = 0.02,
    total_batches_supplied: int = 100,
    flagged_incidents: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "supplier_id": supplier_id,
        "name": "Test Pharma Distributors",
        "reject_rate_3mo": reject_rate_3mo,
        "reject_rate_6mo": reject_rate_6mo,
        "total_batches_supplied": total_batches_supplied,
        "flagged_incidents": flagged_incidents or [],
        "_source": "mock",
    }


# --------------------------------------------------------------------------- #
# Threshold rule engine: no degradation -> NONE, zero LLM calls
# --------------------------------------------------------------------------- #

def test_no_degradation_returns_none_without_calling_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_client(monkeypatch)
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.03, reject_rate_6mo=0.02),
    )

    result = supplier_agent.run_supplier_agent("SUP-001")
    assert isinstance(result, SupplierAlert)
    assert result.severity == "NONE"
    assert result.draft_escalation_message == ""
    assert "No significant degradation" in result.trend_description


def test_degradation_exactly_at_threshold_does_not_trigger_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """delta must be strictly greater than 0.05, per the spec's `> 0.05` rule."""
    _forbid_client(monkeypatch)
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.10, reject_rate_6mo=0.05),
    )

    result = supplier_agent.run_supplier_agent("SUP-001")

    assert result.severity == "NONE"


def test_improving_supplier_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_client(monkeypatch)
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.01, reject_rate_6mo=0.10),
    )

    result = supplier_agent.run_supplier_agent("SUP-001")

    assert result.severity == "NONE"


def test_unknown_supplier_returns_none_without_calling_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_client(monkeypatch)
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: {"error": "No supplier found with id 'SUP-999'.", "source": "mock"},
    )

    result = supplier_agent.run_supplier_agent("SUP-999")

    assert result.severity == "NONE"
    assert "No supplier record found" in result.trend_description
    assert "SUP-999" in result.trend_description


# --------------------------------------------------------------------------- #
# Threshold rule engine: real degradation -> LLM invoked exactly once for analysis
# --------------------------------------------------------------------------- #

def test_degradation_invokes_llm_and_returns_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )

    analysis_resp = _analysis_response(
        trend_description="Reject rate rose from 5% to 20% over the last 3 months.",
        severity="HIGH",
        suggested_action="Place supplier on hold pending review.",
        draft_escalation_message="Please review SUP-001's rising reject rate.",
    )
    fake_client = _patch_client(monkeypatch, [analysis_resp])

    result = supplier_agent.run_supplier_agent("SUP-001")

    assert result.severity == "HIGH"
    assert result.supplier_id == "SUP-001"
    assert result.draft_escalation_message == "Please review SUP-001's rising reject rate."
    assert len(fake_client.models.calls) == 1

    call_kwargs = fake_client.models.calls[0]
    assert call_kwargs["config"].response_schema is supplier_agent.AlertAnalysisResponse
    assert call_kwargs["config"].system_instruction == supplier_agent.ANALYSIS_SYSTEM_PROMPT


def test_invalid_severity_from_llm_defaults_to_medium(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )
    analysis_resp = _analysis_response(
        trend_description="Reject rate rose sharply.",
        severity="EXTREME",  # not a valid enum value
        suggested_action="Escalate.",
        draft_escalation_message="Escalation needed.",
    )
    _patch_client(monkeypatch, [analysis_resp])

    result = supplier_agent.run_supplier_agent("SUP-001")

    assert result.severity == "MEDIUM"


# --------------------------------------------------------------------------- #
# Multilingual Alert Engine
# --------------------------------------------------------------------------- #

def test_lang_hi_translates_draft_escalation_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )

    analysis_resp = _analysis_response(
        trend_description="Reject rate rose from 5% to 20%.",
        severity="HIGH",
        suggested_action="Hold supplier.",
        draft_escalation_message="Please review SUP-001's rising reject rate.",
    )
    translation_resp = _translation_response("कृपया SUP-001 की बढ़ती अस्वीकृति दर की समीक्षा करें।")
    fake_client = _patch_client(monkeypatch, [analysis_resp, translation_resp])

    result = supplier_agent.run_supplier_agent("SUP-001", lang="hi")

    assert len(fake_client.models.calls) == 2
    translation_call = fake_client.models.calls[1]
    assert "Hindi" in translation_call["config"].system_instruction
    # The English draft is what gets sent to the translator.
    assert translation_call["contents"] == "Please review SUP-001's rising reject rate."
    # The final alert carries the translated text, not the English original.
    assert result.draft_escalation_message == "कृपया SUP-001 की बढ़ती अस्वीकृति दर की समीक्षा करें।"


def test_lang_or_uses_odia_in_translation_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )
    analysis_resp = _analysis_response(
        trend_description="Degraded.",
        severity="MEDIUM",
        suggested_action="Review.",
        draft_escalation_message="Escalation text.",
    )
    translation_resp = _translation_response("translated-odia-text")
    fake_client = _patch_client(monkeypatch, [analysis_resp, translation_resp])

    result = supplier_agent.run_supplier_agent("SUP-001", lang="or")

    translation_call = fake_client.models.calls[1]
    assert "Odia" in translation_call["config"].system_instruction
    assert result.draft_escalation_message == "translated-odia-text"


def test_translation_falls_back_to_original_on_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )
    analysis_resp = _analysis_response(
        trend_description="Degraded.",
        severity="MEDIUM",
        suggested_action="Review.",
        draft_escalation_message="Original English escalation.",
    )
    translation_resp = _translation_response("   ")  # whitespace-only
    _patch_client(monkeypatch, [analysis_resp, translation_resp])

    result = supplier_agent.run_supplier_agent("SUP-001", lang="hi")

    assert result.draft_escalation_message == "Original English escalation."


def test_none_severity_never_triggers_translation_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """severity=NONE means no draft message exists, so lang must not cause an LLM call either."""
    _forbid_client(monkeypatch)
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.02, reject_rate_6mo=0.02),
    )

    result = supplier_agent.run_supplier_agent("SUP-001", lang="hi")

    assert result.severity == "NONE"
    assert result.draft_escalation_message == ""


def test_unsupported_lang_raises_before_any_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden_execute_tool(name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        raise AssertionError("execute_tool should not be called for an invalid lang.")

    monkeypatch.setattr(supplier_agent, "execute_tool", _forbidden_execute_tool)
    _forbid_client(monkeypatch)

    with pytest.raises(supplier_agent.UnsupportedLanguageError):
        supplier_agent.run_supplier_agent("SUP-001", lang="fr")


# --------------------------------------------------------------------------- #
# Gemini SDK error -> AgentUnavailableError mapping
# --------------------------------------------------------------------------- #

def test_api_error_during_analysis_maps_to_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )
    err = _make_api_error(500, "server exploded")
    _patch_client(monkeypatch, [err])

    with pytest.raises(supplier_agent.AgentUnavailableError) as excinfo:
        supplier_agent.run_supplier_agent("SUP-001")
    assert "Gemini API error" in str(excinfo.value.reason)


def test_unexpected_exception_during_analysis_maps_to_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )
    err = ConnectionError("connection refused")
    _patch_client(monkeypatch, [err])

    with pytest.raises(supplier_agent.AgentUnavailableError) as excinfo:
        supplier_agent.run_supplier_agent("SUP-001")
    assert "Failed to generate structured agent output" in str(excinfo.value.reason)


def test_missing_api_key_raises_agent_unavailable_when_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )
    # Deliberately not patching _build_client — the real env-var guard should fire.
    with pytest.raises(supplier_agent.AgentUnavailableError):
        supplier_agent.run_supplier_agent("SUP-001")


# NOTE: The original test file also had
# `test_forced_tool_call_without_matching_block_raises_agent_unavailable`,
# exercising a `supplier_agent._call_forced_tool` helper — that function
# doesn't exist in supplier_agent.py. The Gemini implementation enforces a
# single structured JSON response via `response_schema=` on the request
# config rather than a forced-tool-choice retry loop, so there is no
# equivalent "forced tool call with no matching block" failure mode to test
# here. Dropped rather than faked back into existence; see the same note in
# test_qa_agent.py for the analogous `_find_tool_use` case.


# --------------------------------------------------------------------------- #
# Pure helper functions
# --------------------------------------------------------------------------- #

def test_compute_degradation() -> None:
    assert supplier_agent._compute_degradation(0.20, 0.05) == pytest.approx(0.15)
    assert supplier_agent._compute_degradation(0.02, 0.02) == pytest.approx(0.0)


def test_no_degradation_alert_shape() -> None:
    alert = supplier_agent._no_degradation_alert("SUP-001", 0.03, 0.02)
    assert alert.supplier_id == "SUP-001"
    assert alert.severity == "NONE"
    assert alert.draft_escalation_message == ""


def test_not_found_alert_shape() -> None:
    alert = supplier_agent._not_found_alert("SUP-404")
    assert alert.supplier_id == "SUP-404"
    assert alert.severity == "NONE"
    assert "SUP-404" in alert.trend_description
