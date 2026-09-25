"""
services/followups/service.py

High-risk patient follow-up tracking (maternal, child, chronic conditions
— named explicitly in the PS's expected outcomes). A follow-up is a due
date attached to a patient with a reason; "overdue" is computed, not
stored, so the dashboard's overdue count is always live rather than
depending on a background job that might not run during a demo.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime, timezone

from services.ledger.chain import append_event
from services.patients.service import get_patient_or_raise
from shared.schemas import FollowUp, RiskCategory

ACTION_FOLLOWUP_CREATED = "FOLLOWUP_CREATED"
ACTION_FOLLOWUP_COMPLETED = "FOLLOWUP_COMPLETED"


class FollowUpError(Exception):
    """Base for named follow-up errors."""


class FollowUpNotFoundError(FollowUpError):
    def __init__(self):
        super().__init__("Follow-up not found")


def ensure_followups_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS follow_ups (
            follow_up_id   TEXT PRIMARY KEY,
            patient_id     TEXT NOT NULL,
            risk_category  TEXT NOT NULL,
            reason         TEXT NOT NULL,
            due_date       TEXT NOT NULL,
            completed      INTEGER NOT NULL DEFAULT 0,
            completed_at   TEXT,
            created_by     TEXT NOT NULL,
            created_at     TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_followups_patient ON follow_ups (patient_id, due_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_followups_due ON follow_ups (completed, due_date)"
    )


def create_follow_up(
    conn: sqlite3.Connection, *, patient_id: str, risk_category: RiskCategory,
    reason: str, due_date: date, created_by: str,
) -> FollowUp:
    get_patient_or_raise(conn, patient_id)
    follow_up_id = f"FU-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now(timezone.utc)

    conn.execute(
        """
        INSERT INTO follow_ups
            (follow_up_id, patient_id, risk_category, reason, due_date, completed, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (follow_up_id, patient_id, risk_category.value, reason, due_date.isoformat(), created_by, now.isoformat()),
    )
    conn.commit()

    append_event(
        conn, batch_id=patient_id, actor=created_by, action=ACTION_FOLLOWUP_CREATED,
        payload={"follow_up_id": follow_up_id, "risk_category": risk_category.value, "reason": reason, "due_date": due_date.isoformat()},
    )

    return get_follow_up_or_raise(conn, follow_up_id)


def complete_follow_up(conn: sqlite3.Connection, follow_up_id: str, actor: str) -> FollowUp:
    follow_up = get_follow_up_or_raise(conn, follow_up_id)
    now = datetime.now(timezone.utc)
    conn.execute(
        "UPDATE follow_ups SET completed = 1, completed_at = ? WHERE follow_up_id = ?",
        (now.isoformat(), follow_up_id),
    )
    conn.commit()
    append_event(
        conn, batch_id=follow_up.patient_id, actor=actor, action=ACTION_FOLLOWUP_COMPLETED,
        payload={"follow_up_id": follow_up_id},
    )
    return get_follow_up_or_raise(conn, follow_up_id)


def get_follow_up(conn: sqlite3.Connection, follow_up_id: str) -> FollowUp | None:
    row = conn.execute("SELECT * FROM follow_ups WHERE follow_up_id = ?", (follow_up_id,)).fetchone()
    if row is None:
        return None
    return _row_to_followup(row)


def get_follow_up_or_raise(conn: sqlite3.Connection, follow_up_id: str) -> FollowUp:
    follow_up = get_follow_up(conn, follow_up_id)
    if follow_up is None:
        raise FollowUpNotFoundError()
    return follow_up


def list_follow_ups(
    conn: sqlite3.Connection, *, patient_id: str | None = None, overdue_only: bool = False,
) -> list[FollowUp]:
    clauses, params = ["completed = 0"], []
    if patient_id:
        clauses.append("patient_id = ?"); params.append(patient_id)
    if overdue_only:
        clauses.append("due_date < ?"); params.append(date.today().isoformat())
    where = f"WHERE {' AND '.join(clauses)}"
    rows = conn.execute(f"SELECT * FROM follow_ups {where} ORDER BY due_date ASC", params).fetchall()
    return [_row_to_followup(r) for r in rows]


def _row_to_followup(row: sqlite3.Row) -> FollowUp:
    return FollowUp(
        follow_up_id=row["follow_up_id"], patient_id=row["patient_id"],
        risk_category=RiskCategory(row["risk_category"]), reason=row["reason"],
        due_date=date.fromisoformat(row["due_date"]), completed=bool(row["completed"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        created_by=row["created_by"], created_at=datetime.fromisoformat(row["created_at"]),
    )
