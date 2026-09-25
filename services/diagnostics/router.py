"""services/diagnostics/router.py"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.database import get_db
from shared.schemas import DiagnosticResultFlag, DiagnosticStatus
from services.patients.service import PatientNotFoundError
from services.diagnostics.service import (
    DiagnosticOrderNotFoundError,
    InvalidDiagnosticTransitionError,
    NoDiagnosticsCapableFacilityError,
    cancel_order,
    create_diagnostic_order,
    get_diagnostic_order_or_raise,
    list_diagnostic_orders,
    mark_sample_collected,
    record_result,
    review_result,
)

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


def _db_dependency():
    with get_db() as conn:
        yield conn


class CreateDiagnosticOrderRequest(BaseModel):
    patient_id: str
    ordering_facility_id: str
    test_type: str
    reason: str
    ordered_by: str


class SampleCollectedRequest(BaseModel):
    actor: str


class RecordResultRequest(BaseModel):
    result_flag: DiagnosticResultFlag
    result_summary: str
    actor: str


class ReviewRequest(BaseModel):
    actor: str


class CancelRequest(BaseModel):
    actor: str
    reason: str | None = None


@router.post("")
def post_create_order(body: CreateDiagnosticOrderRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        order = create_diagnostic_order(
            db, patient_id=body.patient_id, ordering_facility_id=body.ordering_facility_id,
            test_type=body.test_type, reason=body.reason, ordered_by=body.ordered_by,
        )
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    except NoDiagnosticsCapableFacilityError:
        raise HTTPException(status_code=422, detail="No diagnostics-capable facility could be found to route this order to")
    return order.model_dump(mode="json")


@router.get("")
def get_orders(
    patient_id: str | None = None, performing_facility_id: str | None = None,
    status: DiagnosticStatus | None = None, db: sqlite3.Connection = Depends(_db_dependency),
):
    orders = list_diagnostic_orders(db, patient_id=patient_id, performing_facility_id=performing_facility_id, status=status)
    return [o.model_dump(mode="json") for o in orders]


@router.get("/{diagnostic_id}")
def get_one_order(diagnostic_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        order = get_diagnostic_order_or_raise(db, diagnostic_id)
    except DiagnosticOrderNotFoundError:
        raise HTTPException(status_code=404, detail="Diagnostic order not found")
    return order.model_dump(mode="json")


@router.post("/{diagnostic_id}/sample-collected")
def post_sample_collected(diagnostic_id: str, body: SampleCollectedRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        order = mark_sample_collected(db, diagnostic_id, body.actor)
    except DiagnosticOrderNotFoundError:
        raise HTTPException(status_code=404, detail="Diagnostic order not found")
    except InvalidDiagnosticTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return order.model_dump(mode="json")


@router.post("/{diagnostic_id}/result")
def post_result(diagnostic_id: str, body: RecordResultRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        order = record_result(
            db, diagnostic_id, result_flag=body.result_flag, result_summary=body.result_summary, actor=body.actor,
        )
    except DiagnosticOrderNotFoundError:
        raise HTTPException(status_code=404, detail="Diagnostic order not found")
    except InvalidDiagnosticTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return order.model_dump(mode="json")


@router.post("/{diagnostic_id}/review")
def post_review(diagnostic_id: str, body: ReviewRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        order = review_result(db, diagnostic_id, body.actor)
    except DiagnosticOrderNotFoundError:
        raise HTTPException(status_code=404, detail="Diagnostic order not found")
    except InvalidDiagnosticTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return order.model_dump(mode="json")


@router.post("/{diagnostic_id}/cancel")
def post_cancel(diagnostic_id: str, body: CancelRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        order = cancel_order(db, diagnostic_id, body.actor, body.reason)
    except DiagnosticOrderNotFoundError:
        raise HTTPException(status_code=404, detail="Diagnostic order not found")
    except InvalidDiagnosticTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return order.model_dump(mode="json")
