"""
services/diagnostics/service.py

Diagnostic coordination: lab/imaging test orders for a patient, tracked
from ORDERED through to a reviewed result. This is the PS's "diagnostic
coordination" bullet, and it is a genuinely separate concern from
services/intake/diagnostics.py, which checks photo quality for medicine-
batch OCR scans and has nothing to do with patient tests.

Not every facility can run every test -- a sub-centre has no lab. So
ordering a test does not assume the ordering facility performs it: if it
can't (Facility.has_diagnostics is false), the order is auto-routed to the
nearest facility that can, via find_nearest_facility(..., require_diagnostics=True).
`routed` on the order records whether that happened, so the UI can be
honest with the health worker about where the sample is actually going.

Every transition is appended to the patient's ledger (the same
hash-chained log used by triage, referrals, queue and follow-ups), so a
diagnostic order shows up automatically in the patient's longitudinal
timeline with no separate wiring needed.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from services.facilities.service import find_nearest_facility, get_facility
from services.ledger.chain import append_event
from services.patients.service import get_patient_or_raise
from shared.schemas import DiagnosticOrder, DiagnosticResultFlag, DiagnosticStatus

ACTION_DIAGNOSTIC_ORDERED = "DIAGNOSTIC_ORDERED"
ACTION_DIAGNOSTIC_SAMPLE_COLLECTED = "DIAGNOSTIC_SAMPLE_COLLECTED"
ACTION_DIAGNOSTIC_RESULT_RECORDED = "DIAGNOSTIC_RESULT_RECORDED"
ACTION_DIAGNOSTIC_REVIEWED = "DIAGNOSTIC_REVIEWED"
ACTION_DIAGNOSTIC_CANCELLED = "DIAGNOSTIC_CANCELLED"

_VALID_TRANSITIONS: dict[DiagnosticStatus, set[DiagnosticStatus]] = {
    DiagnosticStatus.ORDERED: {DiagnosticStatus.SAMPLE_COLLECTED, DiagnosticStatus.CANCELLED},
    DiagnosticStatus.SAMPLE_COLLECTED: {DiagnosticStatus.RESULT_AVAILABLE, DiagnosticStatus.CANCELLED},
    DiagnosticStatus.RESULT_AVAILABLE: {DiagnosticStatus.REVIEWED, DiagnosticStatus.CANCELLED},
    DiagnosticStatus.REVIEWED: set(),
    DiagnosticStatus.CANCELLED: set(),
}


class DiagnosticError(Exception):
    """Base for named diagnostics errors."""


class DiagnosticOrderNotFoundError(DiagnosticError):
    def __init__(self):
        super().__init__("Diagnostic order not found")


class InvalidDiagnosticTransitionError(DiagnosticError):
    def __init__(self, from_status: str, to_status: str):
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Cannot move diagnostic order from {from_status} to {to_status}")


class NoDiagnosticsCapableFacilityError(DiagnosticError):
    def __init__(self):
        super().__init__("No diagnostics-capable facility could be found to route this order to")


def ensure_diagnostics_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS diagnostic_orders (
            diagnostic_id           TEXT PRIMARY KEY,
            patient_id              TEXT NOT NULL,
            ordering_facility_id    TEXT NOT NULL,
            performing_facility_id  TEXT NOT NULL,
            routed                  INTEGER NOT NULL DEFAULT 0,
            test_type               TEXT NOT NULL,
            reason                  TEXT NOT NULL,
            status                  TEXT NOT NULL DEFAULT 'ORDERED',
            result_flag             TEXT,
            result_summary          TEXT,
            ordered_by               TEXT NOT NULL,
            reviewed_by             TEXT,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL,
            sample_collected_at     TEXT,
            result_available_at     TEXT,
            reviewed_at             TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_diagnostics_patient ON diagnostic_orders (patient_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_diagnostics_performing ON diagnostic_orders (performing_facility_id, status)"
    )


def create_diagnostic_order(
    conn: sqlite3.Connection, *, patient_id: str, ordering_facility_id: str,
    test_type: str, reason: str, ordered_by: str,
) -> DiagnosticOrder:
    get_patient_or_raise(conn, patient_id)

    ordering_facility = get_facility(conn, ordering_facility_id)
    if ordering_facility is not None and bool(ordering_facility["has_diagnostics"]):
        performing_facility_id = ordering_facility_id
        routed = False
    else:
        nearest = find_nearest_facility(conn, ordering_facility_id, None, require_diagnostics=True)
        if nearest is None:
            raise NoDiagnosticsCapableFacilityError()
        performing_facility_id = nearest.facility["facility_id"]
        routed = True

    diagnostic_id = f"DX-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now(timezone.utc)

    conn.execute(
        """
        INSERT INTO diagnostic_orders
            (diagnostic_id, patient_id, ordering_facility_id, performing_facility_id, routed,
             test_type, reason, status, ordered_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            diagnostic_id, patient_id, ordering_facility_id, performing_facility_id, int(routed),
            test_type, reason, DiagnosticStatus.ORDERED.value, ordered_by, now.isoformat(), now.isoformat(),
        ),
    )
    conn.commit()

    append_event(
        conn, batch_id=patient_id, actor=ordered_by, action=ACTION_DIAGNOSTIC_ORDERED,
        payload={
            "diagnostic_id": diagnostic_id, "test_type": test_type, "reason": reason,
            "ordering_facility_id": ordering_facility_id,
            "performing_facility_id": performing_facility_id, "routed": routed,
        },
    )

    return get_diagnostic_order_or_raise(conn, diagnostic_id)


def mark_sample_collected(conn: sqlite3.Connection, diagnostic_id: str, actor: str) -> DiagnosticOrder:
    order = get_diagnostic_order_or_raise(conn, diagnostic_id)
    _transition(conn, order, DiagnosticStatus.SAMPLE_COLLECTED, actor)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE diagnostic_orders SET sample_collected_at = ? WHERE diagnostic_id = ?",
        (now, diagnostic_id),
    )
    conn.commit()
    append_event(
        conn, batch_id=order.patient_id, actor=actor, action=ACTION_DIAGNOSTIC_SAMPLE_COLLECTED,
        payload={"diagnostic_id": diagnostic_id},
    )
    return get_diagnostic_order_or_raise(conn, diagnostic_id)


def record_result(
    conn: sqlite3.Connection, diagnostic_id: str, *,
    result_flag: DiagnosticResultFlag, result_summary: str, actor: str,
) -> DiagnosticOrder:
    order = get_diagnostic_order_or_raise(conn, diagnostic_id)
    _transition(conn, order, DiagnosticStatus.RESULT_AVAILABLE, actor)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE diagnostic_orders
        SET result_flag = ?, result_summary = ?, result_available_at = ?
        WHERE diagnostic_id = ?
        """,
        (result_flag.value, result_summary, now, diagnostic_id),
    )
    conn.commit()
    append_event(
        conn, batch_id=order.patient_id, actor=actor, action=ACTION_DIAGNOSTIC_RESULT_RECORDED,
        payload={"diagnostic_id": diagnostic_id, "result_flag": result_flag.value, "result_summary": result_summary},
    )
    return get_diagnostic_order_or_raise(conn, diagnostic_id)


def review_result(conn: sqlite3.Connection, diagnostic_id: str, actor: str) -> DiagnosticOrder:
    order = get_diagnostic_order_or_raise(conn, diagnostic_id)
    _transition(conn, order, DiagnosticStatus.REVIEWED, actor)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE diagnostic_orders SET reviewed_by = ?, reviewed_at = ? WHERE diagnostic_id = ?",
        (actor, now, diagnostic_id),
    )
    conn.commit()
    append_event(
        conn, batch_id=order.patient_id, actor=actor, action=ACTION_DIAGNOSTIC_REVIEWED,
        payload={"diagnostic_id": diagnostic_id},
    )
    return get_diagnostic_order_or_raise(conn, diagnostic_id)


def cancel_order(conn: sqlite3.Connection, diagnostic_id: str, actor: str, reason: str | None = None) -> DiagnosticOrder:
    order = get_diagnostic_order_or_raise(conn, diagnostic_id)
    _transition(conn, order, DiagnosticStatus.CANCELLED, actor)
    append_event(
        conn, batch_id=order.patient_id, actor=actor, action=ACTION_DIAGNOSTIC_CANCELLED,
        payload={"diagnostic_id": diagnostic_id, "reason": reason},
    )
    return get_diagnostic_order_or_raise(conn, diagnostic_id)


def _transition(conn: sqlite3.Connection, order: DiagnosticOrder, new_status: DiagnosticStatus, actor: str) -> None:
    if new_status not in _VALID_TRANSITIONS[order.status]:
        raise InvalidDiagnosticTransitionError(order.status.value, new_status.value)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE diagnostic_orders SET status = ?, updated_at = ? WHERE diagnostic_id = ?",
        (new_status.value, now, order.diagnostic_id),
    )
    conn.commit()


def get_diagnostic_order(conn: sqlite3.Connection, diagnostic_id: str) -> DiagnosticOrder | None:
    row = conn.execute(
        "SELECT * FROM diagnostic_orders WHERE diagnostic_id = ?", (diagnostic_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_order(row)


def get_diagnostic_order_or_raise(conn: sqlite3.Connection, diagnostic_id: str) -> DiagnosticOrder:
    order = get_diagnostic_order(conn, diagnostic_id)
    if order is None:
        raise DiagnosticOrderNotFoundError()
    return order


def list_diagnostic_orders(
    conn: sqlite3.Connection, *, patient_id: str | None = None,
    performing_facility_id: str | None = None, status: DiagnosticStatus | None = None,
) -> list[DiagnosticOrder]:
    clauses, params = [], []
    if patient_id:
        clauses.append("patient_id = ?"); params.append(patient_id)
    if performing_facility_id:
        clauses.append("performing_facility_id = ?"); params.append(performing_facility_id)
    if status:
        clauses.append("status = ?"); params.append(status.value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM diagnostic_orders {where} ORDER BY created_at DESC", params
    ).fetchall()
    return [_row_to_order(r) for r in rows]


def _row_to_order(row: sqlite3.Row) -> DiagnosticOrder:
    return DiagnosticOrder(
        diagnostic_id=row["diagnostic_id"], patient_id=row["patient_id"],
        ordering_facility_id=row["ordering_facility_id"],
        performing_facility_id=row["performing_facility_id"], routed=bool(row["routed"]),
        test_type=row["test_type"], reason=row["reason"], status=DiagnosticStatus(row["status"]),
        result_flag=DiagnosticResultFlag(row["result_flag"]) if row["result_flag"] else None,
        result_summary=row["result_summary"], ordered_by=row["ordered_by"],
        reviewed_by=row["reviewed_by"], created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        sample_collected_at=datetime.fromisoformat(row["sample_collected_at"]) if row["sample_collected_at"] else None,
        result_available_at=datetime.fromisoformat(row["result_available_at"]) if row["result_available_at"] else None,
        reviewed_at=datetime.fromisoformat(row["reviewed_at"]) if row["reviewed_at"] else None,
    )
