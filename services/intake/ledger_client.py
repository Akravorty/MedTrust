"""
services/intake/ledger_client.py

Intake never writes to the ledger table directly (Section 17). This
module is the ONLY place that talks to /ledger/log, using an in-process
call to the ledger router's own service functions when running inside
the same FastAPI app (avoids a real HTTP round-trip on localhost for a
hackathon single-process deployment), while still going through the
exact same log_event() contract Person 3 built - never a second, parallel
ledger implementation.

If the ledger call fails for any reason, we swallow it into a boolean
result rather than raising - a ledger outage must never crash intake
scan/finalize (Section 17), and never a raw exception either.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("intake.ledger_client")


def send_ledger_event(conn: sqlite3.Connection, *, batch_id: str, actor: str, action: str, payload: dict) -> bool:
    """Returns True if the event was recorded, False if the ledger call failed."""
    try:
        from services.ledger.service import log_event

        log_event(conn, batch_id=batch_id, actor=actor, action=action, payload=payload)
        logger.info("ledger_event_sent batch_id=%s action=%s", batch_id, action)
        return True
    except Exception:
        logger.warning("ledger_event_failed batch_id=%s action=%s", batch_id, action)
        return False
