"""
services/ledger/service.py

Service-layer glue: named errors (Section 18 / master doc Section 5),
schema bootstrap for main.py/database.py to call, and lifecycle trace
reconstruction from raw events (Section 22) built from actual recorded
actions, not a hardcoded workflow.
"""

import sqlite3

from services.ledger.chain import append_event, ensure_schema, get_trace, verify_chain

# Centralized action constants (Section 12). Extend here, not ad hoc in
# routers, so the vocabulary stays consistent for trace reconstruction.
ACTION_BATCH_CREATED = "BATCH_CREATED"
ACTION_BATCH_FINALIZED = "BATCH_FINALIZED"
ACTION_RISK_EVALUATED = "RISK_EVALUATED"
ACTION_STATUS_CHANGED = "STATUS_CHANGED"
ACTION_RECALL_SIMULATED = "RECALL_SIMULATED"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"

KNOWN_ACTIONS = {
    ACTION_BATCH_CREATED,
    ACTION_BATCH_FINALIZED,
    ACTION_RISK_EVALUATED,
    ACTION_STATUS_CHANGED,
    ACTION_RECALL_SIMULATED,
    ACTION_MANUAL_REVIEW,
}


class LedgerError(Exception):
    """Base for all named ledger errors — routers catch this, never a raw exception."""


class BatchNotFoundError(LedgerError):
    def __init__(self):
        super().__init__("Batch not found")


class LedgerWriteFailedError(LedgerError):
    def __init__(self):
        super().__init__("Ledger write failure")


class TraceUnavailableError(LedgerError):
    def __init__(self):
        super().__init__("Trace unavailable")


class ChainIntegrityFailureError(LedgerError):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__("Chain integrity failure")


class RecallTraceIncompleteError(LedgerError):
    def __init__(self):
        super().__init__("Recall trace incomplete")


def ensure_ledger_schema(conn: sqlite3.Connection) -> None:
    ensure_schema(conn)


def log_event(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    actor: str,
    action: str,
    payload: dict,
    idempotency_key: str | None = None,
) -> sqlite3.Row:
    if not batch_id or not actor or not action:
        raise LedgerError("Invalid request")
    try:
        return append_event(
            conn,
            batch_id=batch_id,
            actor=actor,
            action=action,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad, converted to named error
        raise LedgerWriteFailedError() from exc


def get_verified_trace(conn: sqlite3.Connection, batch_id: str):
    """
    Returns (events, verification_result). Raises BatchNotFoundError if the
    batch has no events at all — the router decides how strict "not found"
    vs "empty trace" should be for its response shape.
    """
    events = get_trace(conn, batch_id)
    if not events:
        raise BatchNotFoundError()
    verification = verify_chain(conn, batch_id)
    return events, verification


# Section 22 — lifecycle reconstruction from actual events, not a hardcoded
# workflow. We simply project the recorded actions in order; the "shape"
# of the lifecycle emerges from what was really logged.
def reconstruct_lifecycle(events: list[sqlite3.Row]) -> list[dict]:
    return [
        {
            "action": e["action"],
            "actor": e["actor"],
            "timestamp": e["timestamp"],
            "event_id": e["event_id"],
        }
        for e in events
    ]


def explain_verification(result) -> str:
    """Human-readable integrity explainer (Section 21), generated from the
    actual ChainVerificationResult — never hardcoded to "verified"."""
    return result.explanation
