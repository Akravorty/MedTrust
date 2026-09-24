"""
shared/database.py

Shared SQLite connection/session layer for MediTrust.

Design:
- One SQLite file, local dev, zero setup (per master doc Section 1).
- A single shared connection per process with `check_same_thread=False`,
  since FastAPI can run request handlers on different threads. SQLite
  itself serializes writes at the file level; correctness beyond that
  (e.g. serializing appends to the same ledger chain) is each service's
  responsibility using their own locking — see services/ledger/chain.py.
- WAL mode enabled for better read/write concurrency during dev.
"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "meditrust.db"

_connection: sqlite3.Connection | None = None
_connection_lock = threading.Lock()


def get_connection() -> sqlite3.Connection:
    """
    Return the process-wide shared SQLite connection, creating it on first
    use. Row access is by column name (sqlite3.Row) so services don't have
    to remember column ordering.
    """
    global _connection
    if _connection is None:
        with _connection_lock:
            if _connection is None:  # re-check inside the lock
                conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA foreign_keys=ON;")
                _connection = conn
    return _connection


@contextmanager
def get_db():
    """
    FastAPI-dependency-friendly context manager. Yields the shared
    connection. Does not close it (the connection is process-lifetime), but
    callers should still use explicit transactions around multi-statement
    writes rather than relying on autocommit.
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        pass


def init_db() -> None:
    """
    Create any tables that don't exist yet. Each service defines its own
    `ensure_*_schema(conn)` and registers it here, rather than this file
    knowing about domain tables directly. Called once at app startup
    (see main.py).
    """
    from services.ledger.service import ensure_ledger_schema
    from services.intake.service import ensure_intake_schema
    from services.risk_engine.storage import ensure_risk_schema, ensure_receipts_schema
    from services.alerts.service import ensure_alerts_schema
    from services.facilities.service import ensure_facilities_schema
    from services.patients.service import ensure_patients_schema
    from services.triage.service import ensure_triage_schema
    from services.referrals.service import ensure_referrals_schema
    from services.queue.service import ensure_queue_schema
    from services.teleconsult.service import ensure_teleconsult_schema
    from services.followups.service import ensure_followups_schema

    conn = get_connection()
    ensure_ledger_schema(conn)
    ensure_intake_schema(conn)
    ensure_risk_schema(conn)
    ensure_receipts_schema(conn)
    ensure_alerts_schema(conn)
    ensure_facilities_schema(conn)
    ensure_patients_schema(conn)
    ensure_triage_schema(conn)
    ensure_referrals_schema(conn)
    ensure_queue_schema(conn)
    ensure_teleconsult_schema(conn)
    ensure_followups_schema(conn)
    conn.commit()