"""
shared/database.py

Shared SQLite connection/session layer for MediTrust.

This file was empty in the repo. services/ledger needs a real DB to write
to, and the integration master doc (Section 1) says all backend modules
share this one file rather than each spinning up their own connection.
Built here minimally and generically on purpose — it knows nothing about
ledgers, batches, or any domain table. Person 1 / Person 2 should be able
to import get_connection()/get_db() the same way and create their own
tables without touching this file.

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
    FastAPI-dependency-friendly context manager. Usage:

        from shared.database import get_db

        @router.post("/ledger/log")
        def log_event(db: sqlite3.Connection = Depends(get_db)):
            ...

    Yields the shared connection. Does not close it (the connection is
    process-lifetime), but callers should still use explicit transactions
    (BEGIN/COMMIT/ROLLBACK) around multi-statement writes rather than
    relying on autocommit.
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        pass


def init_db() -> None:
    """
    Create any tables that don't exist yet. Each service should define its
    own `ensure_schema(conn)`-style function and call it from here, rather
    than this file knowing about domain tables directly. Call this once at
    app startup (see main.py).
    """
    from services.ledger.service import ensure_ledger_schema
    from services.intake.service import ensure_intake_schema
    conn = get_connection()
    ensure_ledger_schema(conn)
    ensure_intake_schema(conn)
    conn.commit()