"""
services/agents

Person 4 (Agentic Layer: Supplier Intelligence + Evidence-Grounded Q&A) for
MediTrust. This package holds the tool layer and the two agents that consume
it (qa_agent, supplier_agent — added in subsequent steps), plus the router
that will expose them over FastAPI (added last).
"""

from .tools import (
    TOOL_DEFINITIONS,
    GetBatchInput,
    GetDecisionInput,
    GetSupplierHistoryInput,
    GetTraceInput,
    execute_tool,
    get_batch,
    get_decision,
    get_supplier_history,
    get_trace,
)

__all__ = [
    "TOOL_DEFINITIONS",
    "GetBatchInput",
    "GetDecisionInput",
    "GetSupplierHistoryInput",
    "GetTraceInput",
    "execute_tool",
    "get_batch",
    "get_decision",
    "get_supplier_history",
    "get_trace",
]

import os
import anthropic

class AgentUnavailableError(Exception):
    """Raised when the LLM service or Anthropic client cannot be invoked."""
    pass

def _build_client():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise AgentUnavailableError("ANTHROPIC_API_KEY is not set.")
    return anthropic.Anthropic(api_key=api_key)

def _compute_degradation(reject_rate_3mo: float, reject_rate_6mo: float) -> float:
    return reject_rate_3mo - reject_rate_6mo

def _no_degradation_alert(supplier_id: str, reject_rate_3mo: float, reject_rate_6mo: float) -> dict:
    return {
        "supplier_id": supplier_id,
        "severity": "NONE",
        "trend_description": f"No significant degradation observed for {supplier_id}.",
        "suggested_action": None,
        "draft_escalation_message": None,
    }

def _not_found_alert(supplier_id: str) -> dict:
    return {
        "supplier_id": supplier_id,
        "severity": "NONE",
        "trend_description": f"Supplier {supplier_id} not found.",
        "suggested_action": None,
        "draft_escalation_message": None,
    }