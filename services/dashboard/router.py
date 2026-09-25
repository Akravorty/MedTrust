"""
services/dashboard/router.py

Facility dashboard: one aggregated read model over patients, triage,
referrals, queue, follow-ups, AND medicine batch availability (MediTrust's
original engine, now one widget among several rather than the whole app).
This directly answers the PS's "facility dashboards" bullet.

All numbers are computed live from existing tables — no separate
materialized/cached KPI table to keep in sync, which matters more for
demo reliability than for query performance at this data size.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends

from shared.database import get_db

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _db_dependency():
    with get_db() as conn:
        yield conn


@router.get("/{facility_id}")
def get_facility_dashboard(facility_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    today = date.today().isoformat()

    patients_today = db.execute(
        "SELECT COUNT(*) AS c FROM patients WHERE home_facility_id = ? AND registered_at LIKE ?",
        (facility_id, f"{today}%"),
    ).fetchone()["c"]

    triaged_today = db.execute(
        """
        SELECT COUNT(*) AS c FROM triage_results t
        JOIN patients p ON p.patient_id = t.patient_id
        WHERE p.home_facility_id = ? AND t.decided_at LIKE ?
        """,
        (facility_id, f"{today}%"),
    ).fetchone()["c"]

    referrals_completed = db.execute(
        "SELECT COUNT(*) AS c FROM referrals WHERE from_facility_id = ? AND status = 'COMPLETED'",
        (facility_id,),
    ).fetchone()["c"]

    referrals_pending = db.execute(
        "SELECT COUNT(*) AS c FROM referrals WHERE to_facility_id = ? AND status IN ('CREATED', 'ACCEPTED', 'IN_TRANSIT')",
        (facility_id,),
    ).fetchone()["c"]

    queue_depth = db.execute(
        "SELECT COUNT(*) AS c FROM queue_tickets WHERE facility_id = ? AND queue_date = ? AND status = 'WAITING'",
        (facility_id, today),
    ).fetchone()["c"]

    high_risk_overdue = db.execute(
        """
        SELECT COUNT(*) AS c FROM follow_ups f
        JOIN patients p ON p.patient_id = f.patient_id
        WHERE p.home_facility_id = ? AND f.completed = 0 AND f.due_date < ?
        """,
        (facility_id, today),
    ).fetchone()["c"]

    emergency_flags_today = db.execute(
        """
        SELECT COUNT(*) AS c FROM triage_results t
        JOIN patients p ON p.patient_id = t.patient_id
        WHERE p.home_facility_id = ? AND t.urgency = 'EMERGENCY' AND t.decided_at LIKE ?
        """,
        (facility_id, f"{today}%"),
    ).fetchone()["c"]

    # Diagnostic coordination widget — scoped to orders THIS facility
    # performs (performing_facility_id), since that's the facility whose
    # lab/imaging workload the number represents, not wherever the order
    # happened to be placed from.
    diagnostics_row = db.execute(
        """
        SELECT
            SUM(CASE WHEN status IN ('ORDERED', 'SAMPLE_COLLECTED') THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN status = 'RESULT_AVAILABLE' THEN 1 ELSE 0 END) AS awaiting_review,
            SUM(CASE WHEN status = 'RESULT_AVAILABLE' AND result_flag = 'CRITICAL' THEN 1 ELSE 0 END) AS critical_awaiting_review
        FROM diagnostic_orders
        WHERE performing_facility_id = ?
        """,
        (facility_id,),
    ).fetchone()

    # Medicine availability widget — MediTrust's original engine, scoped
    # down to a single dashboard card rather than the app's whole flow.
    medicine_row = db.execute(
        """
        SELECT
            COUNT(*) AS total_batches,
            SUM(CASE WHEN status = 'ACCEPTED' THEN 1 ELSE 0 END) AS accepted,
            SUM(CASE WHEN status IN ('HOLD', 'REJECTED') THEN 1 ELSE 0 END) AS flagged
        FROM intake_batches
        """
    ).fetchone()
    total_batches = medicine_row["total_batches"] or 0
    accepted = medicine_row["accepted"] or 0

    return {
        "facility_id": facility_id,
        "date": today,
        "patients_registered_today": patients_today,
        "patients_triaged_today": triaged_today,
        "emergency_flags_today": emergency_flags_today,
        "referrals_completed_total": referrals_completed,
        "referrals_pending_incoming": referrals_pending,
        "queue_depth_now": queue_depth,
        "high_risk_follow_ups_overdue": high_risk_overdue,
        "diagnostics": {
            "pending": diagnostics_row["pending"] or 0,
            "awaiting_review": diagnostics_row["awaiting_review"] or 0,
            "critical_awaiting_review": diagnostics_row["critical_awaiting_review"] or 0,
        },
        "medicine_availability": {
            "total_batches_tracked": total_batches,
            "pass_rate_pct": round((accepted / total_batches) * 100, 1) if total_batches else None,
            "flagged": medicine_row["flagged"] or 0,
        },
    }
