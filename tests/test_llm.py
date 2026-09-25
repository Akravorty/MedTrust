"""tests/test_llm.py — model selection and client construction in services/llm.py."""

from __future__ import annotations

import pytest

from services import llm


def test_default_model_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("QA_AGENT_MODEL", raising=False)
    assert llm.resolve_model("QA_AGENT_MODEL") == llm.DEFAULT_GROQ_MODEL


def test_global_groq_model_applies_to_every_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.delenv("TRIAGE_AGENT_MODEL", raising=False)
    assert llm.resolve_model("TRIAGE_AGENT_MODEL") == "llama-3.3-70b-versatile"


def test_per_agent_override_beats_global(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("SUPPLIER_AGENT_MODEL", "openai/gpt-oss-20b")
    assert llm.resolve_model("SUPPLIER_AGENT_MODEL") == "openai/gpt-oss-20b"


def test_stale_gemini_override_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setenv("QA_AGENT_MODEL", "gemini-2.5-flash")
    assert llm.resolve_model("QA_AGENT_MODEL") == llm.DEFAULT_GROQ_MODEL


def test_build_client_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    class Boom(Exception):
        pass

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(Boom, match="GROQ_API_KEY"):
        llm.build_client(Boom)


def test_build_client_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    assert llm.build_client(RuntimeError) is not None


def test_call_args_tolerates_bad_json() -> None:
    from types import SimpleNamespace

    good = SimpleNamespace(function=SimpleNamespace(name="t", arguments='{"a": 1}'))
    bad = SimpleNamespace(function=SimpleNamespace(name="t", arguments="{oops"))
    not_obj = SimpleNamespace(function=SimpleNamespace(name="t", arguments="[1]"))
    assert llm.call_args(good) == {"a": 1}
    assert llm.call_args(bad) == {}
    assert llm.call_args(not_obj) == {}
