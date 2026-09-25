"""
services/agents/qa_agent.py

Person 4 (Agentic Layer) — Evidence-Grounded Q&A Agent for MediTrust using Groq.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from groq import APIStatusError, Groq
from pydantic import ValidationError

from services import llm
from services.agents.tools import execute_tool
from shared.schemas import AgentResponse

load_dotenv()

logger = logging.getLogger("meditrust.agents.qa_agent")

# Model comes from QA_AGENT_MODEL, else GROQ_MODEL, else the shared default (see services/llm.py).
QA_AGENT_MODEL = llm.resolve_model("QA_AGENT_MODEL")

MAX_TOOL_ITERATIONS = 5
_VALID_CONFIDENCE = {"HIGH", "MEDIUM", "INSUFFICIENT_EVIDENCE"}


class AgentUnavailableError(Exception):
    def __init__(self, reason: str, cause: Optional[Exception] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.cause = cause


SYSTEM_PROMPT = """You are the MediTrust Evidence-Grounded Q&A Agent, embedded in a hospital \
medicine quality-gate system. Hospital staff ask you questions about specific medicine batches, \
their risk decisions, audit trails, and suppliers.

You have four data tools available: get_batch, get_decision, get_trace, and get_supplier_history. \
Use them to gather every fact you need before answering.

STRICT GROUNDING RULES:
1. Every factual claim in your answer must be traceable to a specific tool result you retrieved.
2. If the tools return no data or insufficient details, set confidence to "INSUFFICIENT_EVIDENCE".
3. When ready to answer, call the submit_answer tool exactly once as your final action.
"""

# Tool Declarations (OpenAI-compatible, as Groq expects)
_BATCH_ID_PROP = {"batch_id": {"type": "string", "description": "The batch identifier."}}

_TOOLS = [
    llm.function_tool("get_batch", "Fetch medicine batch details by batch_id.", _BATCH_ID_PROP, ["batch_id"]),
    llm.function_tool("get_decision", "Fetch risk decision details for a batch_id.", _BATCH_ID_PROP, ["batch_id"]),
    llm.function_tool("get_trace", "Fetch audit trace log for a batch_id.", _BATCH_ID_PROP, ["batch_id"]),
    llm.function_tool(
        "get_supplier_history",
        "Fetch supplier metrics and historical ratings by supplier_id.",
        {"supplier_id": {"type": "string", "description": "The supplier identifier."}},
        ["supplier_id"],
    ),
    llm.function_tool(
        "submit_answer",
        "Call this exactly once, as your final action, to submit your grounded answer.",
        {
            "answer": {"type": "string", "description": "The grounded answer to the user's query."},
            "confidence": {
                "type": "string",
                "enum": ["HIGH", "MEDIUM", "INSUFFICIENT_EVIDENCE"],
                "description": "Confidence level based strictly on retrieved tool evidence.",
            },
            "evidence_sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact JSON keys / timestamps from tool results.",
            },
        },
        ["answer", "confidence", "evidence_sources"],
    ),
]


def _build_client() -> Groq:
    return llm.build_client(AgentUnavailableError)


def run_qa_agent(query: str, model: Optional[str] = None) -> AgentResponse:
    client = _build_client()
    active_model = model or QA_AGENT_MODEL

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
        try:
            response = llm.chat(
                client,
                model=active_model,
                messages=messages,
                tools=_TOOLS,
                tool_choice="auto",
                temperature=0.0,
            )
        except APIStatusError as exc:
            logger.error("Groq API Error: %s", exc)
            raise AgentUnavailableError(f"Groq API Error: {str(exc)}", exc) from exc
        except Exception as exc:
            logger.error("Could not connect to Groq API: %s", exc)
            raise AgentUnavailableError(f"Could not connect to Groq API: {str(exc)}", exc) from exc

        message = llm.message_of(response)
        messages.append(llm.assistant_turn(message))
        tool_calls = llm.tool_calls_of(message)

        if tool_calls:
            submit_call = next((c for c in tool_calls if c.function.name == "submit_answer"), None)
            if submit_call is not None:
                return _finalize(query, llm.call_args(submit_call))

            for call in tool_calls:
                result = execute_tool(call.function.name, llm.call_args(call))
                messages.append(llm.tool_result_turn(call, result))
        else:
            messages.append(
                {
                    "role": "user",
                    "content": "You must either call one of the data tools or call submit_answer to finalize your response.",
                }
            )

    return AgentResponse(
        query=query,
        answer="Unable to gather sufficient evidence within the tool-call budget.",
        evidence_sources=[],
        confidence="INSUFFICIENT_EVIDENCE",
    )


def _finalize(query: str, submit_input: Dict[str, Any]) -> AgentResponse:
    confidence = str(submit_input.get("confidence", "")).upper()
    if confidence not in _VALID_CONFIDENCE:
        confidence = "INSUFFICIENT_EVIDENCE"

    evidence_sources = submit_input.get("evidence_sources") or []
    if not isinstance(evidence_sources, list):
        evidence_sources = [str(evidence_sources)]

    raw_answer = str(submit_input.get("answer", "")).strip()
    if not raw_answer:
        # An empty/whitespace-only answer means the model had nothing grounded
        # to say — never let that pass through as HIGH/MEDIUM confidence.
        confidence = "INSUFFICIENT_EVIDENCE"
    answer = raw_answer or "The agent did not produce an answer."

    try:
        return AgentResponse(
            query=query,
            answer=answer,
            evidence_sources=[str(s) for s in evidence_sources],
            confidence=confidence,
        )
    except ValidationError:
        return AgentResponse(
            query=query,
            answer=answer,
            evidence_sources=[],
            confidence="INSUFFICIENT_EVIDENCE",
        )