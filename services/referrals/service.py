"""
services/referrals/service.py

Sub-centre -> PHC -> CHC -> Rural Hospital -> District Hospital referral
tracking. Every status change is logged onto the patient's ledger, so
"referral completion" (one of the PS's stated outcomes) is independently
auditable, not just a status column a facility could edit unnoticed.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from services.ledger.chain import append_event
from services.patients.service import get_patient_or_raise
from shared.schemas import Referral, ReferralStatus, UrgencyBand

ACTION_REFERRAL_CREATED = "REFERRAL_CREATED"
ACTION_REFERRAL_STATUS_CHANGED = "REFERRAL_STATUS_CHANGED"

_VALID_TRANSITIONS = {
    ReferralStatus.CREATED: {ReferralStatus.ACCEPTED, ReferralStatus.CANCELLED},
    ReferralStatus.ACCEPTED: {ReferralStatus.IN_TRANSIT, ReferralStatus.CANCELLED},
    ReferralStatus.IN_TRANSIT: {ReferralStatus.COMPLETED, ReferralStatus.CANCELLED},
    ReferralStatus.COMPLETED: set(),
    ReferralStatus.CANCELLED: set(),
}


class ReferralError(Exception):
    """Base for named referral errors."""


class ReferralNotFoundError(ReferralError):
    def __init__(self):
        super().__init__("Referral not found")


class InvalidReferralTransitionError(ReferralError):
    def __init__(self, from_status: str, to_status: str):
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Cannot move referral from {from_status} to {to_status}")


def ensure_referrals_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS referrals (
            referral_id      TEXT PRIMARY KEY,
            patient_id       TEXT NOT NULL,
            from_facility_id TEXT NOT NULL,
            to_facility_id   TEXT NOT NULL,
            reason           TEXT NOT NULL,
            urgency          TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'CREATED',
            created_by       TEXT NOT NULL,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_referrals_patient ON referrals (patient_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_referrals_facility ON referrals (to_facility_id, status)"
    )


def create_referral(
    conn: sqlite3.Connection, *, patient_id: str, from_facility_id: str, to_facility_id: str,
    reason: str, urgency: UrgencyBand, created_by: str,
) -> Referral:
    get_patient_or_raise(conn, patient_id)
    referral_id = f"REF-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now(timezone.utc)

    conn.execute(
        """
        INSERT INTO referrals
            (referral_id, patient_id, from_facility_id, to_facility_id, reason,
             urgency, status, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            referral_id, patient_id, from_facility_id, to_facility_id, reason,
            urgency.value, ReferralStatus.CREATED.value, created_by, now.isoformat(), now.isoformat(),
        ),
    )
    conn.commit()

    append_event(
        conn, batch_id=patient_id, actor=created_by, action=ACTION_REFERRAL_CREATED,
        payload={
            "referral_id": referral_id, "from_facility_id": from_facility_id,
            "to_facility_id": to_facility_id, "reason": reason, "urgency": urgency.value,
        },
    )

    return get_referral_or_raise(conn, referral_id)


def get_referral(conn: sqlite3.Connection, referral_id: str) -> Referral | None:
    row = conn.execute("SELECT * FROM referrals WHERE referral_id = ?", (referral_id,)).fetchone()
    if row is None:
        return None
    return _row_to_referral(row)


def get_referral_or_raise(conn: sqlite3.Connection, referral_id: str) -> Referral:
    referral = get_referral(conn, referral_id)
    if referral is None:
        raise ReferralNotFoundError()
    return referral


def list_referrals(
    conn: sqlite3.Connection, *, patient_id: str | None = None,
    to_facility_id: str | None = None, status: ReferralStatus | None = None,
) -> list[Referral]:
    clauses, params = [], []
    if patient_id:
        clauses.append("patient_id = ?"); params.append(patient_id)
    if to_facility_id:
        clauses.append("to_facility_id = ?"); params.append(to_facility_id)
    if status:
        clauses.append("status = ?"); params.append(status.value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM referrals {where} ORDER BY created_at DESC", params
    ).fetchall()
    return [_row_to_referral(r) for r in rows]


def update_referral_status(
    conn: sqlite3.Connection, referral_id: str, new_status: ReferralStatus, actor: str
) -> Referral:
    referral = get_referral_or_raise(conn, referral_id)
    if new_status not in _VALID_TRANSITIONS[referral.status]:
        raise InvalidReferralTransitionError(referral.status.value, new_status.value)

    now = datetime.now(timezone.utc)
    conn.execute(
        "UPDATE referrals SET status = ?, updated_at = ? WHERE referral_id = ?",
        (new_status.value, now.isoformat(), referral_id),
    )
    conn.commit()

    append_event(
        conn, batch_id=referral.patient_id, actor=actor, action=ACTION_REFERRAL_STATUS_CHANGED,
        payload={"referral_id": referral_id, "from_status": referral.status.value, "to_status": new_status.value},
    )

    return get_referral_or_raise(conn, referral_id)


def _row_to_referral(row: sqlite3.Row) -> Referral:
    return Referral(
        referral_id=row["referral_id"], patient_id=row["patient_id"],
        from_facility_id=row["from_facility_id"], to_facility_id=row["to_facility_id"],
        reason=row["reason"], urgency=UrgencyBand(row["urgency"]), status=ReferralStatus(row["status"]),
        created_by=row["created_by"], created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
