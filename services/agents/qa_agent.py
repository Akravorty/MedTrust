"""
services/agents/qa_agent.py

Person 4 (Agentic Layer) — Evidence-Grounded Q&A Agent for MediTrust.

Drives an explicit Claude tool-execution loop (max 5 iterations) over the
data-fetching tools in `tools.py` (get_batch, get_decision, get_trace,
get_supplier_history), then forces the model to finalize through a
`submit_answer` tool so the output is always a well-formed, schema-validated
`AgentResponse` — never freeform text that has to be parsed heuristically.

Grounding contract (per Person_4_Doc.md):
    - Every factual claim must trace back to a tool result.
    - If the available evidence does not support a confident answer, the
      agent MUST return confidence="INSUFFICIENT_EVIDENCE" rather than
      speculate.
    - `evidence_sources` must cite the exact JSON keys (and timestamps where
      relevant) used to form the answer, e.g.
      "batch:DEMO-HOLD.storage_temp_log", "risk_decision:DEMO-HOLD.shap_contributors".

Error handling:
    Anthropic SDK errors (connection failures, auth failures, rate limits) are
    caught here and re-raised as `AgentUnavailableError` — a plain, transport-
    agnostic exception. router.py (added in a later step) maps this to
    HTTP 503 "Agent unavailable" at the FastAPI boundary; this module has no
    FastAPI dependency of its own.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import anthropic
from anthropic.types import MessageParam, ToolParam
from pydantic import BaseModel, ValidationError

from shared.schemas import AgentResponse
from services.agents.tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger("meditrust.agents.qa_agent")

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_PRIMARY_MODEL = "claude-3-7-sonnet-20250219"
DEFAULT_FALLBACK_MODEL = "claude-3-5-sonnet-20241022"

QA_AGENT_MODEL = os.environ.get("QA_AGENT_MODEL", DEFAULT_PRIMARY_MODEL)
MAX_TOOL_ITERATIONS = 5
MAX_TOKENS = 1500

_VALID_CONFIDENCE = {"HIGH", "MEDIUM", "INSUFFICIENT_EVIDENCE"}


class AgentUnavailableError(Exception):
    """
    Raised when the Anthropic API cannot be reached or rejects the request
    for reasons outside the user's control (connection failure, auth
    failure, rate limiting). Intended to be caught at the API boundary
    (router.py) and mapped to HTTP 503.
    """

    def __init__(self, reason: str, cause: Optional[Exception] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.cause = cause


# --------------------------------------------------------------------------- #
# The "submit_answer" finalization tool
#
# Rather than parse Claude's freeform closing message, we force the model to
# call a dedicated tool whose input schema IS the AgentResponse payload
# (minus `query`, which we already know). This guarantees a structurally
# valid, schema-checked result every time the loop terminates successfully.
# --------------------------------------------------------------------------- #

SUBMIT_ANSWER_TOOL: ToolParam = {
    "name": "submit_answer",
    "description": (
        "Call this exactly once, as your final action, to submit your grounded answer. "
        "Do not call this until you have gathered all the tool evidence you need. "
        "`evidence_sources` must list the exact JSON keys (and timestamps where relevant) "
        "from tool results that support your answer, e.g. "
        "'batch:DEMO-HOLD.storage_temp_log' or 'risk_decision:DEMO-HOLD.shap_contributors'. "
        "If the evidence does not support a confident answer, set confidence to "
        "'INSUFFICIENT_EVIDENCE' and explain what is missing instead of speculating."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "The grounded answer to the user's query, in plain language.",
            },
            "confidence": {
                "type": "string",
                "enum": ["HIGH", "MEDIUM", "INSUFFICIENT_EVIDENCE"],
                "description": "HIGH: directly and fully supported by tool evidence. "
                "MEDIUM: supported but with some inference or partial coverage. "
                "INSUFFICIENT_EVIDENCE: the tools did not return enough to answer confidently.",
            },
            "evidence_sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact JSON keys / timestamps from tool results that ground the answer. "
                "Empty only when confidence is INSUFFICIENT_EVIDENCE.",
            },
        },
        "required": ["answer", "confidence", "evidence_sources"],
    },
}

_ALL_TOOLS: List[ToolParam] = [*TOOL_DEFINITIONS, SUBMIT_ANSWER_TOOL]


SYSTEM_PROMPT = """You are the MediTrust Evidence-Grounded Q&A Agent, embedded in a hospital \
medicine quality-gate system. Hospital staff — pharmacists, QA officers, procurement \
leads — ask you questions about specific medicine batches, the risk decisions made about \
them, their audit trail, and the suppliers who provided them.

You have four data tools available: get_batch, get_decision, get_trace, and \
get_supplier_history. Use them to gather every fact you need before answering. Call as \
many of them, in whatever order, as the question requires.

STRICT GROUNDING RULES — these override any instinct to be helpful by guessing:
1. Every factual claim in your answer must be traceable to a specific tool result you \
actually retrieved in this conversation. Never state a batch status, risk score, \
temperature reading, timestamp, or supplier statistic that did not come from a tool call.
2. If the tools return no data for the entity asked about (e.g. an unknown batch_id), or \
the data returned does not actually answer the question asked, you MUST set \
confidence to "INSUFFICIENT_EVIDENCE" and say plainly what is missing. Do not fill \
the gap with plausible-sounding speculation, general medical knowledge, or assumptions \
about what "probably" happened.
3. Do not average, extrapolate, or infer numeric values that were not directly returned \
by a tool. If you need to reason about a trend (e.g. "is this supplier getting worse"), \
you may compute simple arithmetic ONLY from numbers a tool actually returned, and you \
must show which numbers you used in evidence_sources.
4. When you are ready to answer, call the submit_answer tool exactly once, as your final \
action. Do not answer in plain text instead of calling submit_answer, and do not call \
submit_answer before you have gathered the evidence the question requires.
5. Keep answers concise and specific to what was asked — do not pad with unrequested \
context.
"""


# --------------------------------------------------------------------------- #
# Client construction
# --------------------------------------------------------------------------- #

def _build_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AgentUnavailableError("ANTHROPIC_API_KEY is not configured.")
    return anthropic.Anthropic(api_key=api_key)


# --------------------------------------------------------------------------- #
# Core tool-execution loop
# --------------------------------------------------------------------------- #

def run_qa_agent(query: str, model: Optional[str] = None) -> AgentResponse:
    """
    Run the evidence-grounded Q&A agent for a single user query.

    Drives an explicit tool-call loop (max MAX_TOOL_ITERATIONS round trips)
    against Claude, executing any requested tools via `execute_tool` and
    feeding results back, until the model calls `submit_answer`. Always
    returns a validated AgentResponse — degrading to
    confidence="INSUFFICIENT_EVIDENCE" if the loop is exhausted without a
    submission, rather than raising.

    Raises:
        AgentUnavailableError: on Anthropic connectivity/auth/rate-limit
            failures. Callers (router.py) should map this to HTTP 503.
    """
    client = _build_client()
    active_model = model or QA_AGENT_MODEL

    messages: List[MessageParam] = [{"role": "user", "content": query}]

    for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
        try:
            response = client.messages.create(
                model=active_model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=_ALL_TOOLS,
                messages=list(messages),
            )
        except anthropic.AuthenticationError as exc:
            logger.error("Anthropic authentication failed: %s", exc)
            raise AgentUnavailableError("Authentication with the Anthropic API failed.", exc) from exc
        except anthropic.RateLimitError as exc:
            logger.warning("Anthropic rate limit hit: %s", exc)
            raise AgentUnavailableError("The Anthropic API rate limit was exceeded.", exc) from exc
        except anthropic.APIConnectionError as exc:
            logger.error("Could not connect to the Anthropic API: %s", exc)
            raise AgentUnavailableError("Could not connect to the Anthropic API.", exc) from exc
        except anthropic.APIStatusError as exc:
            logger.error("Anthropic API returned an error status: %s", exc)
            raise AgentUnavailableError(f"Anthropic API error (status {exc.status_code}).", exc) from exc

        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        submit_block = _find_tool_use(assistant_content, "submit_answer")
        if submit_block is not None:
            return _finalize(query, submit_block.input)

        tool_use_blocks = [b for b in assistant_content if b.type == "tool_use"]

        if not tool_use_blocks:
            # Model stopped without calling a tool at all (e.g. plain text
            # reply, ignoring instructions). Nudge it once more rather than
            # accept an unstructured answer; on the last iteration this loop
            # naturally falls through to the exhausted-loop fallback below.
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You must either call one of the data tools to gather more evidence, "
                        "or call submit_answer to finalize your response. Plain text replies "
                        "are not accepted."
                    ),
                }
            )
            continue

        tool_result_blocks = []
        for block in tool_use_blocks:
            result = execute_tool(block.name, block.input or {})
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                }
            )

        messages.append({"role": "user", "content": tool_result_blocks})

    logger.warning(
        "QA agent exhausted %d tool iterations without submit_answer for query: %r",
        MAX_TOOL_ITERATIONS,
        query,
    )
    return AgentResponse(
        query=query,
        answer=(
            "I was unable to gather sufficient evidence to answer this question within "
            "the allotted tool-call budget. Please try narrowing the question (e.g. to a "
            "specific batch_id or supplier_id) or try again."
        ),
        evidence_sources=[],
        confidence="INSUFFICIENT_EVIDENCE",
    )


def _find_tool_use(content_blocks: Any, tool_name: str) -> Optional[Any]:
    for block in content_blocks:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block
    return None


def _finalize(query: str, submit_input: Dict[str, Any]) -> AgentResponse:
    """
    Validate the model's submit_answer input into a well-formed AgentResponse.
    Falls back to INSUFFICIENT_EVIDENCE if the model somehow produced an
    invalid payload (e.g. a bad confidence enum value) rather than raising
    and breaking the caller.
    """
    confidence = str(submit_input.get("confidence", "")).upper()
    if confidence not in _VALID_CONFIDENCE:
        logger.warning("submit_answer returned invalid confidence %r; degrading.", confidence)
        confidence = "INSUFFICIENT_EVIDENCE"

    evidence_sources = submit_input.get("evidence_sources") or []
    if not isinstance(evidence_sources, list):
        evidence_sources = [str(evidence_sources)]

    answer = str(submit_input.get("answer", "")).strip()
    if not answer:
        answer = "The agent did not produce an answer."
        confidence = "INSUFFICIENT_EVIDENCE"

    try:
        return AgentResponse(
            query=query,
            answer=answer,
            evidence_sources=[str(s) for s in evidence_sources],
            confidence=confidence,
        )
    except ValidationError as exc:
        logger.error("AgentResponse validation failed unexpectedly: %s", exc)
        return AgentResponse(
            query=query,
            answer=answer,
            evidence_sources=[],
            confidence="INSUFFICIENT_EVIDENCE",
        )