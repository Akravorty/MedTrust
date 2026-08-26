"""
services/risk_engine/ledger_client.py

The Risk Engine is a CONSUMER of Person 3's ledger API. It must never write
to the ledger table directly (Integration Master Section 3: "Person 1 and
Person 2 never write directly to the ledger table").

ASSUMPTION TO VERIFY: this calls the ledger router as an in-process FastAPI
call via `requests` against LEDGER_BASE_URL (works whether the ledger router
is mounted in the same `main.py` app or run as a separate service — same
pattern used across the other role docs' mock-first workflow). If the team
is instead using direct in-process function calls between routers (common
for a single-FastAPI-app hackathon setup), swap `_post_ledger_log`'s body
for a direct import of Person 3's service function — flag that change to
the team since it's a cross-module call pattern, not just an internal detail.
"""

from __future__ import annotations

import os
from typing import Optional

import requests

LEDGER_BASE_URL = os.environ.get("LEDGER_BASE_URL", "http://localhost:8000")
LEDGER_LOG_TIMEOUT_SECONDS = 3


class LedgerLogFailedError(Exception):
    """Raised when the ledger call fails. Caller decides whether this should
    surface as 'Risk evaluation unavailable' or be handled more gracefully —
    see router.py for the chosen behavior."""


def log_risk_decision(
    batch_id: str,
    actor: str,
    action: str,
    payload: dict,
) -> Optional[dict]:
    """
    POSTs a structured event to /ledger/log. Returns the created AuditEvent
    dict on success. Raises LedgerLogFailedError on failure — this function
    never silently pretends the event was logged.
    """
    try:
        response = requests.post(
            f"{LEDGER_BASE_URL}/ledger/log",
            json={
                "batch_id": batch_id,
                "actor": actor,
                "action": action,
                "payload": payload,
            },
            timeout=LEDGER_LOG_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise LedgerLogFailedError(f"Failed to log risk decision to ledger: {exc}") from exc
