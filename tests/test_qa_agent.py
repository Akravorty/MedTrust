"""
tests/test_qa_agent.py

Unit tests for services/agents/qa_agent.py.

No real network or Gemini API calls are made: `qa_agent._build_client` and
`qa_agent.execute_tool` are monkeypatched with fakes. `client.models` is
stubbed with a `_FakeModels` object whose `.generate_content(**kwargs)`
returns queued canned responses (or raises a queued exception), matching
the surface qa_agent.py actually calls: `response.candidates[0].content`
and `response.function_calls` (each with `.name` / `.args`) — the
google-genai response shape, not the Anthropic Messages content-block shape.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from google.genai.errors import APIError

from services.agents import qa_agent
from shared.schemas import AgentResponse


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #

def _function_call(name: str, args: Dict[str, Any]) -> SimpleNamespace:
    """Mimics a google.genai function-call object: needs .name and .args."""
    return SimpleNamespace(name=name, args=args)


def _response(
    function_calls: Optional[List[SimpleNamespace]] = None,
    content: Optional[SimpleNamespace] = None,
) -> SimpleNamespace:
    """
    Mimics a google.genai GenerateContentResponse.

    qa_agent.py only reads `response.candidates[0].content` (appended
    verbatim back into the conversation) and `response.function_calls`
    (a list of function-call objects, empty/falsy for a plain-text turn).
    """
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=content or SimpleNamespace())],
        function_calls=function_calls or [],
    )


class _FakeModels:
    """Stands in for `client.models`, returning queued responses or raising a queued exception."""

    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        recorded = dict(kwargs)
        contents = recorded.get("contents")
        if isinstance(contents, list):
            # qa_agent.py keeps appending to the same `contents` list on later
            # iterations, so snapshot it now or later assertions would see
            # appends that happened *after* this call was made.
            recorded["contents"] = list(contents)
        self.calls.append(recorded)
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
    """Most tests replace _build_client entirely; this just keeps the env sane by default."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")


def _patch_client(monkeypatch: pytest.MonkeyPatch, responses: List[Any]) -> _FakeClient:
    fake_client = _FakeClient(responses)
    monkeypatch.setattr(qa_agent, "_build_client", lambda: fake_client)
    return fake_client


def _make_api_error(code: int, message: str) -> APIError:
    return APIError(code, {"error": {"message": message, "status": "ERROR"}})


# --------------------------------------------------------------------------- #
# Happy path: immediate submit_answer, no data tools needed
# --------------------------------------------------------------------------- #

def test_submit_answer_on_first_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    submit_call = _function_call(
        "submit_answer",
        {
            "answer": "Batch DEMO-HOLD was put on hold due to a cold-chain excursion.",
            "confidence": "HIGH",
            "evidence_sources": ["batch:DEMO-HOLD.storage_temp_log"],
        },
    )
    _patch_client(monkeypatch, [_response(function_calls=[submit_call])])

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
    get_batch_call = _function_call("get_batch", {"batch_id": "DEMO-HOLD"})
    submit_call = _function_call(
        "submit_answer",
        {
            "answer": "The batch is currently on HOLD status.",
            "confidence": "HIGH",
            "evidence_sources": ["batch:DEMO-HOLD.status"],
        },
    )
    fake_client = _patch_client(
        monkeypatch,
        [_response(function_calls=[get_batch_call]), _response(function_calls=[submit_call])],
    )

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

    # Two round trips to Gemini: the tool-use turn, then the submit_answer turn.
    assert len(fake_client.models.calls) == 2

    # The second call's contents must carry the function response back to Gemini.
    second_call_contents = fake_client.models.calls[1]["contents"]
    tool_result_content = second_call_contents[-1]
    assert tool_result_content.role == "user"
    part = tool_result_content.parts[0]
    assert part.function_response.name == "get_batch"
    assert part.function_response.response == {"result": fake_tool_result}


def test_multiple_tool_calls_in_one_turn_are_all_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    batch_call = _function_call("get_batch", {"batch_id": "DEMO-HOLD"})
    decision_call = _function_call("get_decision", {"batch_id": "DEMO-HOLD"})
    submit_call = _function_call(
        "submit_answer",
        {
            "answer": "Held per risk engine rule.",
            "confidence": "HIGH",
            "evidence_sources": ["risk_decision:DEMO-HOLD.triggered_rule"],
        },
    )
    _patch_client(
        monkeypatch,
        [_response(function_calls=[batch_call, decision_call]), _response(function_calls=[submit_call])],
    )

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
# Model ignores instructions and replies with plain text (no function call at all)
# --------------------------------------------------------------------------- #

def test_plain_text_turn_is_nudged_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    text_only = _response(function_calls=[])
    submit_call = _function_call(
        "submit_answer",
        {"answer": "Confirmed via tool data.", "confidence": "MEDIUM", "evidence_sources": ["batch:DEMO-ACCEPT.status"]},
    )
    fake_client = _patch_client(monkeypatch, [text_only, _response(function_calls=[submit_call])])
    monkeypatch.setattr(qa_agent, "execute_tool", lambda name, tool_input: {"unused": True})

    result = qa_agent.run_qa_agent("Is DEMO-ACCEPT okay?")

    assert result.confidence == "MEDIUM"
    assert len(fake_client.models.calls) == 2
    # The nudge message must have been appended to the conversation sent on the retry.
    retry_contents = fake_client.models.calls[1]["contents"]
    last_content = retry_contents[-1]
    assert last_content.role == "user"
    assert "submit_answer" in last_content.parts[0].text


# --------------------------------------------------------------------------- #
# Loop exhaustion: model never calls submit_answer
# --------------------------------------------------------------------------- #

def test_loop_exhaustion_returns_insufficient_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    stalling_call = _function_call("get_batch", {"batch_id": "UNKNOWN"})
    # Queue exactly MAX_TOOL_ITERATIONS responses, none of which ever submit.
    responses = [_response(function_calls=[stalling_call]) for _ in range(qa_agent.MAX_TOOL_ITERATIONS)]
    fake_client = _patch_client(monkeypatch, responses)
    monkeypatch.setattr(qa_agent, "execute_tool", lambda name, tool_input: {"error": "not found"})

    result = qa_agent.run_qa_agent("What happened to batch UNKNOWN?")

    assert result.confidence == "INSUFFICIENT_EVIDENCE"
    assert result.evidence_sources == []
    assert len(fake_client.models.calls) == qa_agent.MAX_TOOL_ITERATIONS


# --------------------------------------------------------------------------- #
# Gemini SDK error -> AgentUnavailableError mapping
# --------------------------------------------------------------------------- #

def test_api_error_maps_to_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    err = _make_api_error(401, "invalid api key")
    _patch_client(monkeypatch, [err])

    with pytest.raises(qa_agent.AgentUnavailableError) as excinfo:
        qa_agent.run_qa_agent("Any question")
    assert "Gemini API Error" in str(excinfo.value.reason)


def test_rate_limit_style_api_error_maps_to_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    err = _make_api_error(429, "rate limited")
    _patch_client(monkeypatch, [err])

    with pytest.raises(qa_agent.AgentUnavailableError):
        qa_agent.run_qa_agent("Any question")


def test_unexpected_exception_maps_to_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Anything that isn't a google.genai APIError (e.g. a raw connection failure)
    # should still be caught and converted, not propagate raw.
    err = ConnectionError("connection refused")
    _patch_client(monkeypatch, [err])

    with pytest.raises(qa_agent.AgentUnavailableError) as excinfo:
        qa_agent.run_qa_agent("Any question")
    assert "Could not connect to Gemini API" in str(excinfo.value.reason)


def test_missing_api_key_raises_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
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


# NOTE: The original test file also had `test_find_tool_use_locates_matching_block`
# and `test_find_tool_use_returns_none_when_absent`, exercising a `qa_agent._find_tool_use`
# helper. That function doesn't exist in qa_agent.py — the Gemini implementation finds
# the submit_answer call inline via a generator expression over `response.function_calls`,
# not a separate content-block-scanning helper. Those two tests were testing something
# that was never built (or was removed on purpose) and have been dropped rather than
# faked back into existence. If a `_find_tool_use`-style helper is intentionally wanted
# for readability, it should be added to qa_agent.py first and then re-tested here.
