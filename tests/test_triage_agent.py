"""
tests/test_triage_agent.py

Unit tests for services/triage/agent.py (Groq tool-calling loop). No network:
`triage_agent._build_client` and `triage_agent.execute_triage_tool` are faked,
mirroring tests/test_qa_agent.py.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
import pytest
from groq import APIStatusError

from services.triage import agent as triage_agent
from shared.schemas import FacilityLevel, TriageResult, UrgencyBand

_n = 0


def _call(name: str, args: Any) -> SimpleNamespace:
    global _n
    _n += 1
    return SimpleNamespace(id=f"call_{_n}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _response(calls: Optional[List[SimpleNamespace]] = None) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=calls or None))])


class _FakeCompletions:
    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        recorded = dict(kwargs)
        recorded["messages"] = list(kwargs["messages"])
        self.calls.append(recorded)
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _patch_client(monkeypatch: pytest.MonkeyPatch, responses: List[Any]) -> _FakeCompletions:
    completions = _FakeCompletions(responses)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(triage_agent, "_build_client", lambda: client)
    return completions


_SUBMIT_URGENT = {
    "urgency": "URGENT",
    "suggested_facility_level": "CHC",
    "reasoning": "Chest pain reported; CHC in district can take the patient.",
    "confidence": "HIGH",
    "evidence_sources": ["get_patient_profile", "find_referral_target"],
}


def test_submit_on_first_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = _patch_client(monkeypatch, [_response([_call("submit_triage", _SUBMIT_URGENT)])])

    result = triage_agent.run_triage_agent("P-1", "chest pain")

    assert isinstance(result, TriageResult)
    assert result.urgency == UrgencyBand.URGENT
    assert result.suggested_facility_level == FacilityLevel.CHC
    assert result.confidence == "HIGH"
    assert result.patient_id == "P-1"
    sent = completions.calls[0]
    assert sent["messages"][0]["role"] == "system"
    assert "patient_id: P-1" in sent["messages"][1]["content"]
    assert "chest pain" in sent["messages"][1]["content"]
    assert [t["function"]["name"] for t in sent["tools"]] == [
        "get_patient_profile", "get_patient_history", "get_facility_capacity",
        "find_referral_target", "submit_triage",
    ]


def test_tool_round_trip_then_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = _patch_client(
        monkeypatch,
        [_response([_call("get_patient_profile", {"patient_id": "P-1"})]), _response([_call("submit_triage", _SUBMIT_URGENT)])],
    )
    seen: List[Any] = []
    monkeypatch.setattr(
        triage_agent, "execute_triage_tool",
        lambda name, tool_input: seen.append((name, tool_input)) or {"age": 4, "is_child": True},
    )

    result = triage_agent.run_triage_agent("P-1", "fast breathing")

    assert seen == [("get_patient_profile", {"patient_id": "P-1"})]
    assert result.urgency == UrgencyBand.URGENT
    tool_msg = completions.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert json.loads(tool_msg["content"]) == {"age": 4, "is_child": True}


def test_budget_exhaustion_fails_safe_toward_caution(monkeypatch: pytest.MonkeyPatch) -> None:
    stalling = [_response([_call("get_patient_history", {"patient_id": "P-1"})]) for _ in range(triage_agent.MAX_TOOL_ITERATIONS)]
    _patch_client(monkeypatch, stalling)
    monkeypatch.setattr(triage_agent, "execute_triage_tool", lambda name, tool_input: {"events": []})

    result = triage_agent.run_triage_agent("P-1", "unwell")

    assert result.urgency == UrgencyBand.URGENT
    assert result.suggested_facility_level == FacilityLevel.PHC
    assert result.confidence == "INSUFFICIENT_EVIDENCE"


def test_invalid_model_output_degrades_to_cautious_defaults() -> None:
    result = triage_agent._finalize(
        "P-1", "x",
        {"urgency": "whenever", "suggested_facility_level": "MOON_BASE", "confidence": "SURE", "reasoning": "  "},
    )
    assert result.urgency == UrgencyBand.URGENT
    assert result.suggested_facility_level == FacilityLevel.PHC
    assert result.confidence == "INSUFFICIENT_EVIDENCE"
    assert result.reasoning == "No reasoning provided."


def test_missing_api_key_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(triage_agent.TriageAgentUnavailableError):
        triage_agent.run_triage_agent("P-1", "x")


def test_api_error_maps_to_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    err = APIStatusError("rate limited", response=httpx.Response(429, request=req), body={"error": {"message": "rate limited"}})
    _patch_client(monkeypatch, [err])
    with pytest.raises(triage_agent.TriageAgentUnavailableError) as excinfo:
        triage_agent.run_triage_agent("P-1", "x")
    assert "Groq API Error" in excinfo.value.reason


def test_connection_failure_maps_to_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, [ConnectionError("refused")])
    with pytest.raises(triage_agent.TriageAgentUnavailableError) as excinfo:
        triage_agent.run_triage_agent("P-1", "x")
    assert "Could not connect to Groq API" in excinfo.value.reason
