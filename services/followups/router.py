"""services/followups/router.py"""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.database import get_db
from shared.schemas import RiskCategory
from services.followups.service import (
    FollowUpNotFoundError,
    complete_follow_up,
    create_follow_up,
    list_follow_ups,
)
from services.patients.service import PatientNotFoundError

router = APIRouter(prefix="/followups", tags=["followups"])


def _db_dependency():
    with get_db() as conn:
        yield conn


class CreateFollowUpRequest(BaseModel):
    patient_id: str
    risk_category: RiskCategory
    reason: str
    due_date: date
    created_by: str


class CompleteFollowUpRequest(BaseModel):
    actor: str


@router.post("")
def post_create(body: CreateFollowUpRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        follow_up = create_follow_up(
            db, patient_id=body.patient_id, risk_category=body.risk_category,
            reason=body.reason, due_date=body.due_date, created_by=body.created_by,
        )
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    return follow_up.model_dump(mode="json")


@router.get("")
def get_all(patient_id: str | None = None, overdue_only: bool = False, db: sqlite3.Connection = Depends(_db_dependency)):
    follow_ups = list_follow_ups(db, patient_id=patient_id, overdue_only=overdue_only)
    return [f.model_dump(mode="json") for f in follow_ups]


@router.post("/{follow_up_id}/complete")
def post_complete(follow_up_id: str, body: CompleteFollowUpRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        follow_up = complete_follow_up(db, follow_up_id, body.actor)
    except FollowUpNotFoundError:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    return follow_up.model_dump(mode="json")
