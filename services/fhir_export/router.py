"""services/fhir_export/router.py — FHIR R4 export of a patient's care-access record."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from services.fhir_export.builder import build_patient_bundle
from services.patients.service import PatientNotFoundError
from shared.database import get_db

router = APIRouter(prefix="/fhir", tags=["fhir"])


def _db_dependency():
    with get_db() as conn:
        yield conn


@router.get("/patients/{patient_id}/bundle")
def get_patient_bundle(patient_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    """FHIR R4 Bundle (collection) with the patient, their facilities, referrals
    (ServiceRequest) and teleconsult sessions (Encounter). An export in a standard
    format, not ABHA/ABDM integration."""
    try:
        bundle = build_patient_bundle(db, patient_id)
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    return JSONResponse(content=bundle, media_type="application/fhir+json")
