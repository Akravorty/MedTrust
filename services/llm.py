"""
services/llm.py

Single place where the backend talks to Groq. The QA agent, supplier agent and
triage agent all go through these helpers, so swapping model or provider is a
one-file change and the tool-calling wire format (OpenAI-compatible, which is
what Groq speaks) lives in exactly one spot.

Configuration (all via .env):
    GROQ_API_KEY        required
    GROQ_MODEL          default model for every agent (default: openai/gpt-oss-120b,
                        the same default the /ai/complete dashboard route uses)
    QA_AGENT_MODEL, SUPPLIER_AGENT_MODEL, TRIAGE_AGENT_MODEL
                        optional per-agent overrides. A value that still names a
                        Gemini model (left over from the old setup) is ignored.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from groq import APIStatusError, Groq

load_dotenv()

logger = logging.getLogger("meditrust.llm")

DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


def resolve_model(override_env_var: str) -> str:
    """Per-agent override if set (and not a stale Gemini name), else GROQ_MODEL, else the default."""
    override = os.environ.get(override_env_var, "").strip()
    if override and "gemini" not in override.lower():
        return override
    if override:
        logger.warning("%s=%r is a Gemini model name; ignoring it and using Groq.", override_env_var, override)
    return os.environ.get("GROQ_MODEL", "").strip() or DEFAULT_GROQ_MODEL


def build_client(unavailable_exc: type[Exception]) -> Groq:
    """Groq client from GROQ_API_KEY; raises the caller's own 'unavailable' error if the key is missing."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise unavailable_exc("GROQ_API_KEY is not configured in .env file.")
    return Groq(api_key=api_key)


# --------------------------------------------------------------------------- #
# Tool-calling wire format helpers (OpenAI-compatible)
# --------------------------------------------------------------------------- #

def function_tool(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    """Build one tool declaration in the OpenAI/Groq `tools=[...]` shape."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def _is_tool_use_failed(exc: APIStatusError) -> bool:
    # The SDK puts the error body in the message; also check `.body` in case a version doesn't.
    return "tool_use_failed" in str(exc) or "tool_use_failed" in str(getattr(exc, "body", ""))


def chat(client: Any, **kwargs: Any) -> Any:
    """
    One chat-completions call. Groq occasionally answers 400 `tool_use_failed`
    when the model emits a malformed tool call; that is transient, so retry
    once before letting the error surface to the agent's error mapping.
    """
    try:
        return client.chat.completions.create(**kwargs)
    except APIStatusError as exc:
        if exc.status_code == 400 and _is_tool_use_failed(exc):
            logger.warning("Groq tool_use_failed; retrying once.")
            return client.chat.completions.create(**kwargs)
        raise


def message_of(response: Any) -> Any:
    return response.choices[0].message


def tool_calls_of(message: Any) -> List[Any]:
    return list(getattr(message, "tool_calls", None) or [])


def call_args(call: Any) -> Dict[str, Any]:
    """Tool-call arguments arrive as a JSON string; a malformed one becomes {} rather than a crash."""
    raw = call.function.arguments
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        logger.warning("Could not parse tool arguments for %s: %r", call.function.name, raw)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def assistant_turn(message: Any) -> Dict[str, Any]:
    """Plain-dict copy of the model's turn, so it can be appended to the running transcript."""
    turn: Dict[str, Any] = {"role": "assistant", "content": getattr(message, "content", None) or ""}
    calls = tool_calls_of(message)
    if calls:
        turn["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"},
            }
            for c in calls
        ]
    return turn


def tool_result_turn(call: Any, result: Any) -> Dict[str, Any]:
    return {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, default=str)}


def json_object_completion(
    client: Any,
    *,
    model: str,
    system_prompt: str,
    user_content: str,
    json_schema: Dict[str, Any],
    temperature: float = 0.2,
) -> Optional[str]:
    """
    Ask for a single JSON object matching `json_schema` and return the raw text.
    JSON mode plus the schema spelled out in the system prompt works on every
    Groq chat model (native json_schema mode is only on some), and callers
    validate the result themselves.
    """
    schema_text = json.dumps(json_schema, ensure_ascii=False)
    response = chat(
        client,
        model=model,
        messages=[
            {
                "role": "system",
                "content": f"{system_prompt}\n\nRespond with a single JSON object that matches this JSON schema, "
                           f"and nothing else:\n{schema_text}",
            },
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    return message_of(response).content
