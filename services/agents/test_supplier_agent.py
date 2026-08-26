"""
tests/test_supplier_agent.py

Unit tests for services/agents/supplier_agent.py.

No real network or Anthropic API calls are made: `supplier_agent._build_client`
and `supplier_agent.execute_tool` are monkeypatched with fakes, following the
same pattern as tests/test_qa_agent.py. Several tests assert that the LLM is
NOT invoked at all when the threshold rule engine short-circuits — that's the
core cost/latency guarantee this module makes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import anthropic
import httpx
import pytest

from services.agents import supplier_agent
from shared.schemas import SupplierAlert


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #

def _tool_use_block(name: str, input_: Dict[str, Any], block_id: str = "toolu_01") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=input_, id=block_id)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _response(content_blocks: List[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(content=content_blocks)


def _fake_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _fake_httpx_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code=status_code, request=_fake_request())


class _FakeMessages:
    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("_FakeMessages.create called more times than responses were queued.")
        next_item = self._responses.pop(0)
        if isinstance(next_item, BaseException):
            raise next_item
        return next_item


class _FakeClient:
    def __init__(self, responses: List[Any]) -> None:
        self.messages = _FakeMessages(responses)


@pytest.fixture(autouse=True)
def _api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")


def _patch_client(monkeypatch: pytest.MonkeyPatch, responses: List[Any]) -> _FakeClient:
    fake_client = _FakeClient(responses)
    monkeypatch.setattr(supplier_agent, "_build_client", lambda: fake_client)
    return fake_client


def _forbid_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch _build_client to fail loudly if the rule engine incorrectly invokes the LLM."""

    def _raise() -> Any:
        raise AssertionError("_build_client should not be called when severity is NONE.")

    monkeypatch.setattr(supplier_agent, "_build_client", _raise)


def _supplier_payload(
    supplier_id: str = "SUP-001",
    reject_rate_3mo: float = 0.02,
    reject_rate_6mo: float = 0.02,
    total_batches_supplied: int = 100,
    flagged_incidents: List[str] | None = None,
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

    submit_alert_block = _tool_use_block(
        "submit_alert",
        {
            "trend_description": "Reject rate rose from 5% to 20% over the last 3 months.",
            "severity": "HIGH",
            "suggested_action": "Place supplier on hold pending review.",
            "draft_escalation_message": "Please review SUP-001's rising reject rate.",
        },
    )
    fake_client = _patch_client(monkeypatch, [_response([submit_alert_block])])

    result = supplier_agent.run_supplier_agent("SUP-001")

    assert result.severity == "HIGH"
    assert result.supplier_id == "SUP-001"
    assert result.draft_escalation_message == "Please review SUP-001's rising reject rate."
    assert len(fake_client.messages.calls) == 1

    call_kwargs = fake_client.messages.calls[0]
    assert call_kwargs["tool_choice"] == {"type": "tool", "name": "submit_alert"}
    assert call_kwargs["tools"] == [supplier_agent.SUBMIT_ALERT_TOOL]


def test_invalid_severity_from_llm_defaults_to_medium(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )
    submit_alert_block = _tool_use_block(
        "submit_alert",
        {
            "trend_description": "Reject rate rose sharply.",
            "severity": "EXTREME",  # not a valid enum value
            "suggested_action": "Escalate.",
            "draft_escalation_message": "Escalation needed.",
        },
    )
    _patch_client(monkeypatch, [_response([submit_alert_block])])

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

    submit_alert_block = _tool_use_block(
        "submit_alert",
        {
            "trend_description": "Reject rate rose from 5% to 20%.",
            "severity": "HIGH",
            "suggested_action": "Hold supplier.",
            "draft_escalation_message": "Please review SUP-001's rising reject rate.",
        },
    )
    submit_translation_block = _tool_use_block(
        "submit_translation",
        {"translated_text": "कृपया SUP-001 की बढ़ती अस्वीकृति दर की समीक्षा करें।"},
    )
    fake_client = _patch_client(
        monkeypatch, [_response([submit_alert_block]), _response([submit_translation_block])]
    )

    result = supplier_agent.run_supplier_agent("SUP-001", lang="hi")

    assert len(fake_client.messages.calls) == 2
    translation_call = fake_client.messages.calls[1]
    assert translation_call["tool_choice"] == {"type": "tool", "name": "submit_translation"}
    assert translation_call["tools"] == [supplier_agent.SUBMIT_TRANSLATION_TOOL]
    # The English draft is what gets sent to the translator.
    assert translation_call["messages"][0]["content"] == "Please review SUP-001's rising reject rate."
    # The final alert carries the translated text, not the English original.
    assert result.draft_escalation_message == submit_translation_block.input["translated_text"]


def test_lang_or_uses_odia_in_translation_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )
    submit_alert_block = _tool_use_block(
        "submit_alert",
        {
            "trend_description": "Degraded.",
            "severity": "MEDIUM",
            "suggested_action": "Review.",
            "draft_escalation_message": "Escalation text.",
        },
    )
    submit_translation_block = _tool_use_block("submit_translation", {"translated_text": "translated-odia-text"})
    fake_client = _patch_client(
        monkeypatch, [_response([submit_alert_block]), _response([submit_translation_block])]
    )

    result = supplier_agent.run_supplier_agent("SUP-001", lang="or")

    translation_call = fake_client.messages.calls[1]
    assert "Odia" in translation_call["system"]
    assert result.draft_escalation_message == "translated-odia-text"


def test_translation_falls_back_to_original_on_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )
    submit_alert_block = _tool_use_block(
        "submit_alert",
        {
            "trend_description": "Degraded.",
            "severity": "MEDIUM",
            "suggested_action": "Review.",
            "draft_escalation_message": "Original English escalation.",
        },
    )
    submit_translation_block = _tool_use_block("submit_translation", {"translated_text": "   "})
    _patch_client(monkeypatch, [_response([submit_alert_block]), _response([submit_translation_block])])

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
# Anthropic SDK error -> AgentUnavailableError mapping
# --------------------------------------------------------------------------- #

def test_connection_error_during_analysis_maps_to_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )
    err = anthropic.APIConnectionError(message="connection refused", request=_fake_request())
    _patch_client(monkeypatch, [err])

    with pytest.raises(supplier_agent.AgentUnavailableError):
        supplier_agent.run_supplier_agent("SUP-001")


def test_rate_limit_error_during_analysis_maps_to_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )
    err = anthropic.RateLimitError("rate limited", response=_fake_httpx_response(429), body=None)
    _patch_client(monkeypatch, [err])

    with pytest.raises(supplier_agent.AgentUnavailableError):
        supplier_agent.run_supplier_agent("SUP-001")


def test_missing_api_key_raises_agent_unavailable_when_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        supplier_agent,
        "execute_tool",
        lambda name, tool_input: _supplier_payload(reject_rate_3mo=0.20, reject_rate_6mo=0.05),
    )
    # Deliberately not patching _build_client — the real env-var guard should fire.
    with pytest.raises(supplier_agent.AgentUnavailableError):
        supplier_agent.run_supplier_agent("SUP-001")


def test_forced_tool_call_without_matching_block_raises_agent_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeClient([_response([_text_block("I refuse to use tools.")])])

    with pytest.raises(supplier_agent.AgentUnavailableError):
        supplier_agent._call_forced_tool(
            client=fake_client,
            system="system prompt",
            user_content="user content",
            tool=supplier_agent.SUBMIT_ALERT_TOOL,
            model="claude-3-7-sonnet-20250219",
        )


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