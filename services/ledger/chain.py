"""
services/ledger/chain.py

Core append-only, hash-chained event storage + real verification.

Concurrency safety (Section 8 of the Person 3 prompt):
SQLite on one file already serializes writes at the connection level, but
that's not enough by itself — two near-simultaneous appends to the SAME
batch could both read the same "latest event" before either commits,
producing two events with the same prev_hash (a fork, not a chain). We
prevent this with a per-batch in-process threading.Lock so appends to one
batch_id are fully serialized, combined with an explicit
BEGIN IMMEDIATE transaction so the read-latest -> compute -> insert
sequence is atomic even under sqlite's own locking. This is a single
FastAPI process (hackathon scope, Section 20 — no Kafka/Redis needed);
if MediTrust ever ran multiple processes, this lock would need to move to
a DB-level lock (e.g. a SELECT ... FOR UPDATE equivalent) instead.
"""

import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from services.ledger.hashing import (
    GENESIS_HASH,
    compute_event_hash,
    compute_payload_hash,
)

# One lock per batch_id, created lazily. Guards the read-latest -> append
# sequence for that batch so concurrent requests can't both build on the
# same prev_hash.
_batch_locks: dict[str, threading.Lock] = {}
_batch_locks_guard = threading.Lock()


def _lock_for_batch(batch_id: str) -> threading.Lock:
    with _batch_locks_guard:
        if batch_id not in _batch_locks:
            _batch_locks[batch_id] = threading.Lock()
        return _batch_locks[batch_id]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            event_id      TEXT PRIMARY KEY,
            batch_id      TEXT NOT NULL,
            actor         TEXT NOT NULL,
            action        TEXT NOT NULL,
            payload       TEXT NOT NULL,
            payload_hash  TEXT NOT NULL,
            prev_hash     TEXT NOT NULL,
            this_hash     TEXT NOT NULL,
            timestamp     TEXT NOT NULL,
            seq           INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_batch "
        "ON audit_events (batch_id, seq)"
    )
    # Idempotency support (Section 7) — see append_event() docstring.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_event_idempotency (
            idempotency_key TEXT PRIMARY KEY,
            event_id        TEXT NOT NULL
        )
        """
    )


def _latest_event(conn: sqlite3.Connection, batch_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM audit_events WHERE batch_id = ? ORDER BY seq DESC LIMIT 1",
        (batch_id,),
    ).fetchone()


def _next_seq(conn: sqlite3.Connection, batch_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq FROM audit_events WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    return row["next_seq"]


class LedgerWriteError(Exception):
    """Raised when an append fails and should not be treated as success."""


def append_event(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    actor: str,
    action: str,
    payload: dict,
    idempotency_key: str | None = None,
) -> sqlite3.Row:
    """
    Append one AuditEvent to batch_id's chain. Atomic per Section 19:
    read-latest, compute hashes, insert, commit — or roll back entirely.

    Idempotency (Section 7): the current public /ledger/log contract takes
    {batch_id, actor, action, payload} with no explicit idempotency field,
    so we cannot invent a new required public field without a contract
    change. Internally, we accept an OPTIONAL idempotency_key (derived by
    the router from a request header if present, e.g. `Idempotency-Key`,
    or left None). If a key is given and has been seen before for this
    batch, we return the original event instead of writing a duplicate.
    If no key is given, every call is treated as a genuinely new event
    (Section 7 explicitly says not to sacrifice legitimate repeats just
    because payloads look similar) — callers that need retry-safety should
    pass a key derived from something stable on their end (e.g. their own
    request id). This keeps the public contract untouched while giving
    Person 1/2 a safe retry path if/when they adopt it.
    """
    lock = _lock_for_batch(batch_id)
    with lock:
        try:
            conn.execute("BEGIN IMMEDIATE")

            if idempotency_key is not None:
                existing = conn.execute(
                    "SELECT event_id FROM audit_event_idempotency WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    event = conn.execute(
                        "SELECT * FROM audit_events WHERE event_id = ?",
                        (existing["event_id"],),
                    ).fetchone()
                    conn.execute("ROLLBACK")
                    return event

            prev_row = _latest_event(conn, batch_id)
            prev_hash = prev_row["this_hash"] if prev_row is not None else GENESIS_HASH

            event_id = str(uuid.uuid4())
            timestamp = datetime.now(timezone.utc).isoformat()
            payload_hash = compute_payload_hash(payload)
            this_hash = compute_event_hash(
                event_id=event_id,
                batch_id=batch_id,
                actor=actor,
                action=action,
                payload_hash=payload_hash,
                prev_hash=prev_hash,
                timestamp=timestamp,
            )
            seq = _next_seq(conn, batch_id)

            conn.execute(
                """
                INSERT INTO audit_events
                    (event_id, batch_id, actor, action, payload, payload_hash,
                     prev_hash, this_hash, timestamp, seq)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    batch_id,
                    actor,
                    action,
                    _serialize_payload(payload),
                    payload_hash,
                    prev_hash,
                    this_hash,
                    timestamp,
                    seq,
                ),
            )
            if idempotency_key is not None:
                conn.execute(
                    "INSERT INTO audit_event_idempotency (idempotency_key, event_id) VALUES (?, ?)",
                    (idempotency_key, event_id),
                )

            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise LedgerWriteError(str(exc)) from exc

        return conn.execute(
            "SELECT * FROM audit_events WHERE event_id = ?", (event_id,)
        ).fetchone()


def _serialize_payload(payload: dict) -> str:
    from services.ledger.hashing import canonicalize

    return canonicalize(payload)


@dataclass
class ChainVerificationResult:
    valid: bool
    total_events: int
    failed_event_id: str | None = None
    failure_type: str | None = None  # e.g. "payload_hash_mismatch"
    failure_detail: str | None = None
    explanation: str = ""
    event_results: list[dict] = field(default_factory=list)


def verify_chain(conn: sqlite3.Connection, batch_id: str) -> ChainVerificationResult:
    """
    Walk the FULL ordered chain for batch_id and independently recompute
    every hash. This genuinely recomputes cryptographic values — it does
    NOT just check `prev_hash is not None` (Section 9 explicitly forbids
    that shortcut).

    Checks, in order, for every event:
      1. payload_hash matches a fresh hash of the stored payload
      2. this_hash matches a freshly recomputed hash of the event's fields
      3. prev_hash equals the previous event's this_hash (or GENESIS for
         the first event)
    """
    rows = conn.execute(
        "SELECT * FROM audit_events WHERE batch_id = ? ORDER BY seq ASC",
        (batch_id,),
    ).fetchall()

    event_results = []
    expected_prev_hash = GENESIS_HASH

    for idx, row in enumerate(rows, start=1):
        payload = _deserialize_payload(row["payload"])
        recomputed_payload_hash = compute_payload_hash(payload)
        recomputed_this_hash = compute_event_hash(
            event_id=row["event_id"],
            batch_id=row["batch_id"],
            actor=row["actor"],
            action=row["action"],
            payload_hash=row["payload_hash"],
            prev_hash=row["prev_hash"],
            timestamp=row["timestamp"],
        )

        failure = None
        if row["prev_hash"] != expected_prev_hash:
            failure = ("prev_hash_mismatch", f"expected {expected_prev_hash}, found {row['prev_hash']}")
        elif recomputed_payload_hash != row["payload_hash"]:
            failure = (
                "payload_hash_mismatch",
                f"expected {row['payload_hash']}, recomputed {recomputed_payload_hash}",
            )
        elif recomputed_this_hash != row["this_hash"]:
            failure = (
                "this_hash_mismatch",
                f"expected {row['this_hash']}, recomputed {recomputed_this_hash}",
            )

        event_results.append(
            {
                "position": idx,
                "event_id": row["event_id"],
                "action": row["action"],
                "verified": failure is None,
                "failure_type": failure[0] if failure else None,
            }
        )

        if failure is not None:
            return ChainVerificationResult(
                valid=False,
                total_events=len(rows),
                failed_event_id=row["event_id"],
                failure_type=failure[0],
                failure_detail=failure[1],
                explanation=(
                    f"Chain integrity compromised. Event at position {idx} "
                    f"(id={row['event_id']}, action={row['action']}) failed check "
                    f"'{failure[0]}': {failure[1]}."
                ),
                event_results=event_results,
            )

        expected_prev_hash = row["this_hash"]

    explanation = (
        "Chain integrity verified. Every recorded event matches its cryptographic "
        "hash and correctly references the previous event."
        if rows
        else "No events recorded for this batch yet."
    )
    return ChainVerificationResult(
        valid=True,
        total_events=len(rows),
        explanation=explanation,
        event_results=event_results,
    )


def _deserialize_payload(payload_str: str) -> dict:
    import json

    return json.loads(payload_str)


def get_trace(conn: sqlite3.Connection, batch_id: str) -> list[sqlite3.Row]:
    """Full ordered event history for a batch (does not itself verify)."""
    return conn.execute(
        "SELECT * FROM audit_events WHERE batch_id = ? ORDER BY seq ASC",
        (batch_id,),
    ).fetchall()
