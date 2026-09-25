"""services/patients/router.py — registration + longitudinal record endpoints."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.database import get_db
from shared.schemas import RiskCategory
from services.patients.service import (
    PatientNotFoundError,
    get_patient_or_raise,
    get_patient_timeline,
    list_patients,
    register_patient,
    set_risk_category,
    verify_patient_record,
)

router = APIRouter(prefix="/patients", tags=["patients"])


def _db_dependency():
    with get_db() as conn:
        yield conn


class RegisterPatientRequest(BaseModel):
    name: str
    age: int
    gender: str
    village: str
    home_facility_id: str
    registered_by: str
    phone: str | None = None


class SetRiskCategoryRequest(BaseModel):
    risk_category: RiskCategory
    actor: str
    reason: str


@router.post("")
def post_register_patient(body: RegisterPatientRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    patient = register_patient(
        db, name=body.name, age=body.age, gender=body.gender, village=body.village,
        home_facility_id=body.home_facility_id, registered_by=body.registered_by, phone=body.phone,
    )
    return patient.model_dump(mode="json")


@router.get("")
def get_all_patients(facility_id: str | None = None, db: sqlite3.Connection = Depends(_db_dependency)):
    return [p.model_dump(mode="json") for p in list_patients(db, facility_id)]


@router.get("/{patient_id}")
def get_one_patient(patient_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        patient = get_patient_or_raise(db, patient_id)
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient.model_dump(mode="json")


@router.get("/{patient_id}/timeline")
def get_timeline(patient_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        events = get_patient_timeline(db, patient_id)
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"patient_id": patient_id, "events": events}


@router.get("/{patient_id}/verify")
def get_verify(patient_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        result = verify_patient_record(db, patient_id)
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {
        "patient_id": patient_id,
        "valid": result.valid,
        "total_events": result.total_events,
        "explanation": result.explanation,
    }


@router.patch("/{patient_id}/risk-category")
def patch_risk_category(patient_id: str, body: SetRiskCategoryRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        patient = set_risk_category(db, patient_id, body.risk_category, body.actor, body.reason)
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient.model_dump(mode="json")
