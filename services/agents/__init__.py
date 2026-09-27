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
