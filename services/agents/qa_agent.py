"""
services/agents/qa_agent.py

Person 4 (Agentic Layer) — Evidence-Grounded Q&A Agent for MediTrust using Gemini.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import ValidationError

from services.agents.tools import execute_tool
from shared.schemas import AgentResponse

load_dotenv()

logger = logging.getLogger("meditrust.agents.qa_agent")

# Model is set via .env (GEMINI_MODEL) — see .env.example. Falls back to a
# known-stable default if unset.
DEFAULT_PRIMARY_MODEL = "gemini-3.6-flash"
QA_AGENT_MODEL = os.environ.get("GEMINI_MODEL", DEFAULT_PRIMARY_MODEL)

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

# Tool Declarations
get_batch_decl = types.FunctionDeclaration(
    name="get_batch",
    description="Fetch medicine batch details by batch_id.",
    parameters={
        "type": "OBJECT",
        "properties": {"batch_id": {"type": "STRING", "description": "The batch identifier."}},
        "required": ["batch_id"],
    },
)

get_decision_decl = types.FunctionDeclaration(
    name="get_decision",
    description="Fetch risk decision details for a batch_id.",
    parameters={
        "type": "OBJECT",
        "properties": {"batch_id": {"type": "STRING", "description": "The batch identifier."}},
        "required": ["batch_id"],
    },
)

get_trace_decl = types.FunctionDeclaration(
    name="get_trace",
    description="Fetch audit trace log for a batch_id.",
    parameters={
        "type": "OBJECT",
        "properties": {"batch_id": {"type": "STRING", "description": "The batch identifier."}},
        "required": ["batch_id"],
    },
)

get_supplier_history_decl = types.FunctionDeclaration(
    name="get_supplier_history",
    description="Fetch supplier metrics and historical ratings by supplier_id.",
    parameters={
        "type": "OBJECT",
        "properties": {"supplier_id": {"type": "STRING", "description": "The supplier identifier."}},
        "required": ["supplier_id"],
    },
)

submit_answer_decl = types.FunctionDeclaration(
    name="submit_answer",
    description="Call this exactly once, as your final action, to submit your grounded answer.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "answer": {"type": "STRING", "description": "The grounded answer to the user's query."},
            "confidence": {
                "type": "STRING",
                "enum": ["HIGH", "MEDIUM", "INSUFFICIENT_EVIDENCE"],
                "description": "Confidence level based strictly on retrieved tool evidence.",
            },
            "evidence_sources": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Exact JSON keys / timestamps from tool results.",
            },
        },
        "required": ["answer", "confidence", "evidence_sources"],
    },
)

_GEMINI_TOOLS = types.Tool(
    function_declarations=[
        get_batch_decl,
        get_decision_decl,
        get_trace_decl,
        get_supplier_history_decl,
        submit_answer_decl,
    ]
)


def _build_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise AgentUnavailableError("GEMINI_API_KEY is not configured in .env file.")
    return genai.Client(api_key=api_key)


def run_qa_agent(query: str, model: Optional[str] = None) -> AgentResponse:
    client = _build_client()
    active_model = model or QA_AGENT_MODEL

    contents: List[Any] = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=query)],
        )
    ]

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.0,
        tools=[_GEMINI_TOOLS],
    )

    for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
        try:
            response = client.models.generate_content(
                model=active_model,
                contents=contents,
                config=config,
            )
        except APIError as exc:
            logger.error("Gemini API Error: %s", exc)
            raise AgentUnavailableError(f"Gemini API Error: {str(exc)}", exc) from exc
        except Exception as exc:
            logger.error("Could not connect to Gemini API: %s", exc)
            raise AgentUnavailableError(f"Could not connect to Gemini API: {str(exc)}", exc) from exc

        if response.candidates and response.candidates[0].content:
            contents.append(response.candidates[0].content)

        if response.function_calls:
            submit_call = next(
                (call for call in response.function_calls if call.name == "submit_answer"),
                None,
            )
            if submit_call is not None:
                # Fix 2: Cast protobuf struct args to dict
                args = dict(submit_call.args) if submit_call.args else {}
                return _finalize(query, args)

            function_responses = []
            for call in response.function_calls:
                call_args = dict(call.args) if call.args else {}
                result = execute_tool(call.name, call_args)
                function_responses.append(
                    types.Part.from_function_response(
                        name=call.name,
                        response={"result": result},
                    )
                )

            # Fix 3: Use role="user" for returning function responses
            contents.append(types.Content(role="user", parts=function_responses))
        else:
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text="You must either call one of the data tools or call submit_answer to finalize your response."
                        )
                    ],
                )
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