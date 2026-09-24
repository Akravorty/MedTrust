"""services/triage/service.py — persistence + ledger logging for triage results."""

from __future__ import annotations

import sqlite3

from services.ledger.chain import append_event
from services.patients.service import PatientNotFoundError, get_patient_or_raise
from services.triage.agent import TriageAgentUnavailableError, run_triage_agent
from shared.schemas import TriageResult

ACTION_TRIAGE_COMPLETED = "TRIAGE_COMPLETED"


def ensure_triage_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS triage_results (
            triage_id                 TEXT PRIMARY KEY,
            patient_id                TEXT NOT NULL,
            symptoms_text             TEXT NOT NULL,
            urgency                   TEXT NOT NULL,
            suggested_facility_level  TEXT NOT NULL,
            reasoning                 TEXT NOT NULL,
            evidence_sources          TEXT NOT NULL,
            confidence                TEXT NOT NULL,
            decided_at                TEXT NOT NULL,
            model_version             TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_triage_patient ON triage_results (patient_id, decided_at)"
    )


def run_and_store_triage(conn: sqlite3.Connection, *, patient_id: str, symptoms_text: str, actor: str) -> TriageResult:
    get_patient_or_raise(conn, patient_id)  # fail fast with a named error, not a Gemini call on a bad id

    result = run_triage_agent(patient_id, symptoms_text)

    import json
    conn.execute(
        """
        INSERT INTO triage_results
            (triage_id, patient_id, symptoms_text, urgency, suggested_facility_level,
             reasoning, evidence_sources, confidence, decided_at, model_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.triage_id, result.patient_id, result.symptoms_text, result.urgency.value,
            result.suggested_facility_level.value, result.reasoning,
            json.dumps(result.evidence_sources), result.confidence,
            result.decided_at.isoformat(), result.model_version,
        ),
    )
    conn.commit()

    append_event(
        conn, batch_id=patient_id, actor=actor, action=ACTION_TRIAGE_COMPLETED,
        payload={
            "triage_id": result.triage_id, "urgency": result.urgency.value,
            "suggested_facility_level": result.suggested_facility_level.value,
            "confidence": result.confidence,
        },
    )

    return result


def get_latest_triage(conn: sqlite3.Connection, patient_id: str) -> TriageResult | None:
    row = conn.execute(
        "SELECT * FROM triage_results WHERE patient_id = ? ORDER BY decided_at DESC LIMIT 1",
        (patient_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_result(row)


def _row_to_result(row: sqlite3.Row) -> TriageResult:
    import json
    from datetime import datetime
    from shared.schemas import FacilityLevel, UrgencyBand

    return TriageResult(
        triage_id=row["triage_id"], patient_id=row["patient_id"], symptoms_text=row["symptoms_text"],
        urgency=UrgencyBand(row["urgency"]), suggested_facility_level=FacilityLevel(row["suggested_facility_level"]),
        reasoning=row["reasoning"], evidence_sources=json.loads(row["evidence_sources"]),
        confidence=row["confidence"], decided_at=datetime.fromisoformat(row["decided_at"]),
        model_version=row["model_version"],
    )
