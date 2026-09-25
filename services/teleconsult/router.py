"""services/teleconsult/router.py"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.database import get_db
from services.patients.service import PatientNotFoundError
from services.teleconsult.service import (
    SessionNotFoundError,
    complete_session,
    get_session_or_raise,
    list_sessions_for_patient,
    schedule_session,
    start_session,
)

router = APIRouter(prefix="/teleconsult", tags=["teleconsult"])


def _db_dependency():
    with get_db() as conn:
        yield conn


class ScheduleSessionRequest(BaseModel):
    patient_id: str
    facility_id: str
    doctor_name: str
    referral_id: str | None = None


class CompleteSessionRequest(BaseModel):
    notes: str


@router.post("")
def post_schedule(body: ScheduleSessionRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        session = schedule_session(
            db, patient_id=body.patient_id, facility_id=body.facility_id,
            doctor_name=body.doctor_name, referral_id=body.referral_id,
        )
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    return session.model_dump(mode="json")


@router.post("/{session_id}/start")
def post_start(session_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        session = start_session(db, session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.model_dump(mode="json")


@router.post("/{session_id}/complete")
def post_complete(session_id: str, body: CompleteSessionRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        session = complete_session(db, session_id, body.notes)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.model_dump(mode="json")


@router.get("/patient/{patient_id}")
def get_for_patient(patient_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    return [s.model_dump(mode="json") for s in list_sessions_for_patient(db, patient_id)]


@router.get("/{session_id}")
def get_one(session_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        session = get_session_or_raise(db, session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.model_dump(mode="json")
