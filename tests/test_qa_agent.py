"""
tests/test_qa_agent.py

Unit tests for services/agents/qa_agent.py.

No real network or Anthropic API calls are made: `qa_agent._build_client`
and `qa_agent.execute_tool` are monkeypatched with fakes. Anthropic response
objects are stubbed with `types.SimpleNamespace` matching the small surface
qa_agent actually reads (`.content`, and per-block `.type` / `.name` /
`.input` / `.id`), so tests don't depend on constructing real SDK response
objects.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List

import anthropic
import httpx
import pytest

from services.agents import qa_agent
from shared.schemas import AgentResponse


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
    """Stands in for `client.messages`, returning queued responses or raising a queued exception."""

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
    """Most tests replace _build_client entirely; this just keeps the env sane by default."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")


def _patch_client(monkeypatch: pytest.MonkeyPatch, responses: List[Any]) -> _FakeClient:
    fake_client = _FakeClient(responses)
    monkeypatch.setattr(qa_agent, "_build_client", lambda: fake_client)
    return fake_client


# --------------------------------------------------------------------------- #
# Happy path: immediate submit_answer, no data tools needed
# --------------------------------------------------------------------------- #

def test_submit_answer_on_first_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    submit_block = _tool_use_block(
        "submit_answer",
        {
            "answer": "Batch DEMO-HOLD was put on hold due to a cold-chain excursion.",
            "confidence": "HIGH",
            "evidence_sources": ["batch:DEMO-HOLD.storage_temp_log"],
        },
    )
    _patch_client(monkeypatch, [_response([submit_block])])

    result = qa_agent.run_qa_agent("Why was DEMO-HOLD put on hold?")

    assert isinstance(result, AgentResponse)
    assert result.query == "Why was DEMO-HOLD put on hold?"
    assert result.confidence == "HIGH"
    assert result.evidence_sources == ["batch:DEMO-HOLD.storage_temp_log"]
    assert "cold-chain" in result.answer


# --------------------------------------------------------------------------- #
# Happy path: one data-tool round trip before submit_answer
# --------------------------------------------------------------------------- #

def test_tool_call_then_submit_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    get_batch_call = _tool_use_block("get_batch", {"batch_id": "DEMO-HOLD"}, block_id="toolu_batch")
    submit_call = _tool_use_block(
        "submit_answer",
        {
            "answer": "The batch is currently on HOLD status.",
            "confidence": "HIGH",
            "evidence_sources": ["batch:DEMO-HOLD.status"],
        },
        block_id="toolu_submit",
    )
    fake_client = _patch_client(monkeypatch, [_response([get_batch_call]), _response([submit_call])])

    fake_tool_result = {"batch_id": "DEMO-HOLD", "status": "HOLD", "_source": "mock"}
    recorded_tool_calls: List[Any] = []

    def _fake_execute_tool(name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        recorded_tool_calls.append((name, tool_input))
        return fake_tool_result

    monkeypatch.setattr(qa_agent, "execute_tool", _fake_execute_tool)

    result = qa_agent.run_qa_agent("What is the status of DEMO-HOLD?")

    assert result.confidence == "HIGH"
    assert result.evidence_sources == ["batch:DEMO-HOLD.status"]

    # Tool was actually invoked with the model's requested input.
    assert recorded_tool_calls == [("get_batch", {"batch_id": "DEMO-HOLD"})]

    # Two round trips to Claude: the tool-use turn, then the submit_answer turn.
    assert len(fake_client.messages.calls) == 2

    # The second call's message history must carry the tool_result back to Claude.
    second_call_messages = fake_client.messages.calls[1]["messages"]
    tool_result_message = second_call_messages[-1]
    assert tool_result_message["role"] == "user"
    tool_result_block = tool_result_message["content"][0]
    assert tool_result_block["type"] == "tool_result"
    assert tool_result_block["tool_use_id"] == "toolu_batch"
    assert json.loads(tool_result_block["content"]) == fake_tool_result


def test_multiple_tool_calls_in_one_turn_are_all_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    batch_call = _tool_use_block("get_batch", {"batch_id": "DEMO-HOLD"}, block_id="toolu_a")
    decision_call = _tool_use_block("get_decision", {"batch_id": "DEMO-HOLD"}, block_id="toolu_b")
    submit_call = _tool_use_block(
        "submit_answer",
        {"answer": "Held per risk engine rule.", "confidence": "HIGH", "evidence_sources": ["risk_decision:DEMO-HOLD.triggered_rule"]},
        block_id="toolu_c",
    )
    _patch_client(monkeypatch, [_response([batch_call, decision_call]), _response([submit_call])])

    recorded: List[str] = []
    monkeypatch.setattr(
        qa_agent,
        "execute_tool",
        lambda name, tool_input: recorded.append(name) or {"ok": True, "for": name},
    )

    result = qa_agent.run_qa_agent("Why was it held?")

    assert set(recorded) == {"get_batch", "get_decision"}
    assert result.confidence == "HIGH"


# --------------------------------------------------------------------------- #
# Model ignores instructions and replies with plain text (no tool_use at all)
# --------------------------------------------------------------------------- #

def test_plain_text_turn_is_nudged_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    text_only = _response([_text_block("The batch looks fine to me.")])
    submit_call = _tool_use_block(
        "submit_answer",
        {"answer": "Confirmed via tool data.", "confidence": "MEDIUM", "evidence_sources": ["batch:DEMO-ACCEPT.status"]},
    )
    fake_client = _patch_client(monkeypatch, [text_only, _response([submit_call])])
    monkeypatch.setattr(qa_agent, "execute_tool", lambda name, tool_input: {"unused": True})

    result = qa_agent.run_qa_agent("Is DEMO-ACCEPT okay?")

    assert result.confidence == "MEDIUM"
    assert len(fake_client.messages.calls) == 2
    # The nudge message must have been appended to the conversation sent on the retry.
    retry_messages = fake_client.messages.calls[1]["messages"]
    assert retry_messages[-1]["role"] == "user"
    assert "submit_answer" in retry_messages[-1]["content"]


# --------------------------------------------------------------------------- #
# Loop exhaustion: model never calls submit_answer
# --------------------------------------------------------------------------- #

def test_loop_exhaustion_returns_insufficient_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    stalling_call = _tool_use_block("get_batch", {"batch_id": "UNKNOWN"})
    # Queue exactly MAX_TOOL_ITERATIONS responses, none of which ever submit.
    responses = [_response([stalling_call]) for _ in range(qa_agent.MAX_TOOL_ITERATIONS)]
    fake_client = _patch_client(monkeypatch, responses)
    monkeypatch.setattr(qa_agent, "execute_tool", lambda name, tool_input: {"error": "not found"})

    result = qa_agent.run_qa_agent("What happened to batch UNKNOWN?")

    assert result.confidence == "INSUFFICIENT_EVIDENCE"
    assert result.evidence_sources == []
    assert len(fake_client.messages.calls) == qa_agent.MAX_TOOL_ITERATIONS


# --------------------------------------------------------------------------- #
# Anthropic SDK error -> AgentUnavailableError mapping
# --------------------------------------------------------------------------- #

def test_authentication_error_maps_to_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    err = anthropic.AuthenticationError("invalid api key", response=_fake_httpx_response(401), body=None)
    _patch_client(monkeypatch, [err])

    with pytest.raises(qa_agent.AgentUnavailableError):
        qa_agent.run_qa_agent("Any question")


def test_rate_limit_error_maps_to_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    err = anthropic.RateLimitError("rate limited", response=_fake_httpx_response(429), body=None)
    _patch_client(monkeypatch, [err])

    with pytest.raises(qa_agent.AgentUnavailableError):
        qa_agent.run_qa_agent("Any question")


def test_connection_error_maps_to_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    err = anthropic.APIConnectionError(message="connection refused", request=_fake_request())
    _patch_client(monkeypatch, [err])

    with pytest.raises(qa_agent.AgentUnavailableError):
        qa_agent.run_qa_agent("Any question")


def test_generic_api_status_error_maps_to_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    err = anthropic.APIStatusError("server exploded", response=_fake_httpx_response(500), body=None)
    _patch_client(monkeypatch, [err])

    with pytest.raises(qa_agent.AgentUnavailableError):
        qa_agent.run_qa_agent("Any question")


def test_missing_api_key_raises_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Deliberately do NOT patch _build_client here — we want the real
    # implementation's own env-var guard to fire.
    with pytest.raises(qa_agent.AgentUnavailableError):
        qa_agent.run_qa_agent("Any question")


# --------------------------------------------------------------------------- #
# _finalize: defensive validation of the model's submit_answer payload
# --------------------------------------------------------------------------- #

def test_finalize_accepts_well_formed_input() -> None:
    result = qa_agent._finalize(
        "q",
        {"answer": "ok", "confidence": "high", "evidence_sources": ["batch:X.field"]},
    )
    assert result.confidence == "HIGH"  # normalized to uppercase
    assert result.evidence_sources == ["batch:X.field"]


def test_finalize_degrades_invalid_confidence() -> None:
    result = qa_agent._finalize(
        "q",
        {"answer": "ok", "confidence": "SUPER_SURE", "evidence_sources": []},
    )
    assert result.confidence == "INSUFFICIENT_EVIDENCE"


def test_finalize_degrades_empty_answer() -> None:
    result = qa_agent._finalize(
        "q",
        {"answer": "   ", "confidence": "HIGH", "evidence_sources": ["batch:X.field"]},
    )
    assert result.confidence == "INSUFFICIENT_EVIDENCE"
    assert result.answer == "The agent did not produce an answer."


def test_finalize_coerces_non_list_evidence_sources() -> None:
    result = qa_agent._finalize(
        "q",
        {"answer": "ok", "confidence": "MEDIUM", "evidence_sources": "batch:X.field"},
    )
    assert result.evidence_sources == ["batch:X.field"]


# --------------------------------------------------------------------------- #
# _find_tool_use helper
# --------------------------------------------------------------------------- #

def test_find_tool_use_locates_matching_block() -> None:
    blocks = [_text_block("hi"), _tool_use_block("get_batch", {"batch_id": "X"}), _tool_use_block("submit_answer", {})]
    found = qa_agent._find_tool_use(blocks, "submit_answer")
    assert found is not None
    assert found.name == "submit_answer"


def test_find_tool_use_returns_none_when_absent() -> None:
    blocks = [_text_block("hi"), _tool_use_block("get_batch", {"batch_id": "X"})]
    assert qa_agent._find_tool_use(blocks, "submit_answer") is None