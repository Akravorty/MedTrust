"""services/referrals/router.py"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.database import get_db
from shared.schemas import ReferralStatus, UrgencyBand
from services.patients.service import PatientNotFoundError
from services.referrals.service import (
    InvalidReferralTransitionError,
    ReferralNotFoundError,
    create_referral,
    get_referral_or_raise,
    list_referrals,
    update_referral_status,
)

router = APIRouter(prefix="/referrals", tags=["referrals"])


def _db_dependency():
    with get_db() as conn:
        yield conn


class CreateReferralRequest(BaseModel):
    patient_id: str
    from_facility_id: str
    to_facility_id: str
    reason: str
    urgency: UrgencyBand
    created_by: str


class UpdateReferralStatusRequest(BaseModel):
    status: ReferralStatus
    actor: str


@router.post("")
def post_create_referral(body: CreateReferralRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        referral = create_referral(
            db, patient_id=body.patient_id, from_facility_id=body.from_facility_id,
            to_facility_id=body.to_facility_id, reason=body.reason, urgency=body.urgency,
            created_by=body.created_by,
        )
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    return referral.model_dump(mode="json")


@router.get("")
def get_referrals(
    patient_id: str | None = None, to_facility_id: str | None = None,
    status: ReferralStatus | None = None, db: sqlite3.Connection = Depends(_db_dependency),
):
    referrals = list_referrals(db, patient_id=patient_id, to_facility_id=to_facility_id, status=status)
    return [r.model_dump(mode="json") for r in referrals]


@router.get("/{referral_id}")
def get_one_referral(referral_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        referral = get_referral_or_raise(db, referral_id)
    except ReferralNotFoundError:
        raise HTTPException(status_code=404, detail="Referral not found")
    return referral.model_dump(mode="json")


@router.patch("/{referral_id}/status")
def patch_status(referral_id: str, body: UpdateReferralStatusRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        referral = update_referral_status(db, referral_id, body.status, body.actor)
    except ReferralNotFoundError:
        raise HTTPException(status_code=404, detail="Referral not found")
    except InvalidReferralTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return referral.model_dump(mode="json")
