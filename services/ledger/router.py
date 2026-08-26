"""
services/ledger/router.py

Implements the three documented endpoints exactly as specified in the
master doc Section 3:
    POST /ledger/log
    GET  /ledger/trace/{batch_id}
    POST /ledger/recall/simulate/{batch_id}

No raw exceptions ever reach the caller (Section 18) — every failure path
maps to one of the master doc's named errors (Section 5) via HTTPException
with a clean `detail` string.
"""

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from shared.database import get_db
from services.ledger.service import (
    BatchNotFoundError,
    ChainIntegrityFailureError,
    LedgerError,
    LedgerWriteFailedError,
    RecallTraceIncompleteError,
    TraceUnavailableError,
    explain_verification,
    get_verified_trace,
    log_event,
    reconstruct_lifecycle,
)
from services.ledger.recall import simulate_recall

router = APIRouter(prefix="/ledger", tags=["ledger"])


def _db_dependency():
    with get_db() as conn:
        yield conn


@router.post("/log")
def post_log(
    request: Request,
    body: dict,
    db: sqlite3.Connection = Depends(_db_dependency),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """
    Accepts {batch_id, actor, action, payload} per the master doc's
    described contract. `payload` is a free-form dict — its shape is up to
    the caller (Person 1/Person 2 decide what's meaningful to record for
    their own actions).
    """
    batch_id = body.get("batch_id")
    actor = body.get("actor")
    action = body.get("action")
    payload = body.get("payload", {})

    if not batch_id or not actor or not action:
        raise HTTPException(status_code=422, detail="Invalid request")

    try:
        event = log_event(
            db,
            batch_id=batch_id,
            actor=actor,
            action=action,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except LedgerWriteFailedError:
        raise HTTPException(status_code=500, detail="Ledger write failure")
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {
        "event_id": event["event_id"],
        "batch_id": event["batch_id"],
        "actor": event["actor"],
        "action": event["action"],
        "payload_hash": event["payload_hash"],
        "prev_hash": event["prev_hash"],
        "this_hash": event["this_hash"],
        "timestamp": event["timestamp"],
    }


@router.get("/trace/{batch_id}")
def get_trace_endpoint(batch_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        events, verification = get_verified_trace(db, batch_id)
    except BatchNotFoundError:
        raise HTTPException(status_code=404, detail="Batch not found")
    except TraceUnavailableError:
        raise HTTPException(status_code=500, detail="Trace unavailable")

    return {
        "batch_id": batch_id,
        "events": [
            {
                "event_id": e["event_id"],
                "actor": e["actor"],
                "action": e["action"],
                "payload_hash": e["payload_hash"],
                "prev_hash": e["prev_hash"],
                "this_hash": e["this_hash"],
                "timestamp": e["timestamp"],
            }
            for e in events
        ],
        "lifecycle": reconstruct_lifecycle(events),
        "chain_valid": verification.valid,
        "chain_explanation": explain_verification(verification),
    }


@router.post("/recall/simulate/{batch_id}")
def post_recall_simulate(
    batch_id: str,
    body: dict | None = None,
    db: sqlite3.Connection = Depends(_db_dependency),
):
    triggered_by = (body or {}).get("triggered_by", "system")
    try:
        result = simulate_recall(db, batch_id=batch_id, triggered_by=triggered_by)
    except RecallTraceIncompleteError:
        raise HTTPException(status_code=422, detail="Recall trace incomplete")
    except ChainIntegrityFailureError as exc:
        raise HTTPException(status_code=409, detail="Chain integrity failure")
    except LedgerError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return result
