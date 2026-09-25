"""services/triage/router.py"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.database import get_db
from services.patients.service import PatientNotFoundError
from services.triage.agent import TriageAgentUnavailableError
from services.triage.service import get_latest_triage, run_and_store_triage

router = APIRouter(prefix="/triage", tags=["triage"])


def _db_dependency():
    with get_db() as conn:
        yield conn


class RunTriageRequest(BaseModel):
    patient_id: str
    symptoms_text: str
    actor: str  # ASHA / worker id or name running the triage


@router.post("")
def post_run_triage(body: RunTriageRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        result = run_and_store_triage(
            db, patient_id=body.patient_id, symptoms_text=body.symptoms_text, actor=body.actor
        )
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    except TriageAgentUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"Triage agent unavailable: {exc.reason}")
    return result.model_dump(mode="json")


@router.get("/{patient_id}/latest")
def get_latest(patient_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    result = get_latest_triage(db, patient_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No triage result for this patient yet")
    return result.model_dump(mode="json")
