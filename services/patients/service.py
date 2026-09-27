"""
services/patients/service.py

Patient registration and the longitudinal record. The "longitudinal" part
is not a bolt-on log table — it's the same hash-chained ledger MediTrust
already built for batch audit trails (services/ledger/chain.py), keyed by
patient_id instead of batch_id. Every triage, referral, queue, teleconsult
and follow-up event for a patient is appended there, so a patient's full
cross-facility history is tamper-evident and independently verifiable,
not just a mutable row a facility could quietly edit.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from shared.schemas import Patient, RiskCategory
from services.ledger.chain import append_event, ensure_schema as ensure_ledger_schema, get_trace, verify_chain

ACTION_PATIENT_REGISTERED = "PATIENT_REGISTERED"
ACTION_RISK_CATEGORY_SET = "RISK_CATEGORY_SET"


class PatientError(Exception):
    """Base for named patient errors."""


class PatientNotFoundError(PatientError):
    def __init__(self):
        super().__init__("Patient not found")


def ensure_patients_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS patients (
            patient_id       TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            age              INTEGER NOT NULL,
            gender           TEXT NOT NULL,
            village          TEXT NOT NULL,
            phone            TEXT,
            home_facility_id TEXT NOT NULL,
            risk_category    TEXT NOT NULL DEFAULT 'NONE',
            registered_by    TEXT NOT NULL,
            registered_at    TEXT NOT NULL
        )
        """
    )
    # The ledger's own tables (audit_events, audit_event_idempotency) are
    # shared infrastructure — already created by ensure_ledger_schema() in
    # main.py's init_db(). Calling it again here is harmless (CREATE TABLE
    # IF NOT EXISTS) and keeps this module self-contained if it's ever
    # imported standalone.
    ensure_ledger_schema(conn)


def register_patient(
    conn: sqlite3.Connection,
    *,
    name: str,
    age: int,
    gender: str,
    village: str,
    home_facility_id: str,
    registered_by: str,
    phone: str | None = None,
    risk_category: RiskCategory = RiskCategory.NONE,
) -> Patient:
    patient_id = f"PAT-{uuid.uuid4().hex[:8].upper()}"
    registered_at = datetime.now(timezone.utc)

    conn.execute(
        """
        INSERT INTO patients
            (patient_id, name, age, gender, village, phone, home_facility_id,
             risk_category, registered_by, registered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            patient_id, name, age, gender, village, phone, home_facility_id,
            risk_category.value, registered_by, registered_at.isoformat(),
        ),
    )
    conn.commit()

    append_event(
        conn,
        batch_id=patient_id,  # ledger is subject-agnostic; patient_id fills the same slot batch_id does
        actor=registered_by,
        action=ACTION_PATIENT_REGISTERED,
        payload={
            "name": name, "age": age, "gender": gender, "village": village,
            "home_facility_id": home_facility_id, "risk_category": risk_category.value,
        },
    )

    return get_patient(conn, patient_id)


def get_patient(conn: sqlite3.Connection, patient_id: str) -> Patient | None:
    row = conn.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
    if row is None:
        return None
    return Patient(
        patient_id=row["patient_id"], name=row["name"], age=row["age"], gender=row["gender"],
        village=row["village"], phone=row["phone"], home_facility_id=row["home_facility_id"],
        risk_category=RiskCategory(row["risk_category"]), registered_by=row["registered_by"],
        registered_at=datetime.fromisoformat(row["registered_at"]),
    )


def get_patient_or_raise(conn: sqlite3.Connection, patient_id: str) -> Patient:
    patient = get_patient(conn, patient_id)
    if patient is None:
        raise PatientNotFoundError()
    return patient


def list_patients(conn: sqlite3.Connection, facility_id: str | None = None) -> list[Patient]:
    if facility_id:
        rows = conn.execute(
            "SELECT patient_id FROM patients WHERE home_facility_id = ? ORDER BY registered_at DESC",
            (facility_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT patient_id FROM patients ORDER BY registered_at DESC").fetchall()
    return [get_patient(conn, r["patient_id"]) for r in rows]


def set_risk_category(
    conn: sqlite3.Connection, patient_id: str, risk_category: RiskCategory, actor: str, reason: str
) -> Patient:
    get_patient_or_raise(conn, patient_id)
    conn.execute(
        "UPDATE patients SET risk_category = ? WHERE patient_id = ?",
        (risk_category.value, patient_id),
    )
    conn.commit()
    append_event(
        conn, batch_id=patient_id, actor=actor, action=ACTION_RISK_CATEGORY_SET,
        payload={"risk_category": risk_category.value, "reason": reason},
    )
    return get_patient(conn, patient_id)


def get_patient_timeline(conn: sqlite3.Connection, patient_id: str) -> list[dict]:
    """Full cross-facility event history for a patient, oldest first. This
    is what makes the record 'longitudinal': every module (triage,
    referral, queue, teleconsult, follow-up) appends here with the same
    patient_id, so one call returns the whole journey regardless of which
    facility touched the patient."""
    get_patient_or_raise(conn, patient_id)
    events = get_trace(conn, patient_id)
    return [
        {
            "event_id": e["event_id"],
            "actor": e["actor"],
            "action": e["action"],
            "timestamp": e["timestamp"],
        }
        for e in events
    ]


def verify_patient_record(conn: sqlite3.Connection, patient_id: str):
    get_patient_or_raise(conn, patient_id)
    return verify_chain(conn, patient_id)
