"""
services/intake/router.py

Implements the frozen Intake API contract exactly:
    POST /intake/scan
    POST /intake/batches/{id}/finalize
    GET  /intake/batches/{id}

No raw exceptions ever reach the caller - every failure path maps to a
named error (Section 5/18) via HTTPException with a clean detail string.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from shared.database import get_db
from services.intake.service import (
    AlreadyFinalizedError,
    BatchNotFoundError,
    InvalidImageIntakeError,
    ManualReviewFinalizeError,
    finalize_batch,
    get_batch_or_raise,
    scan_batch,
)

router = APIRouter(prefix="/intake", tags=["intake"])


def _db_dependency():
    with get_db() as conn:
        yield conn


def _batch_to_response(batch) -> dict:
    return {
        "batch_id": batch.batch_id,
        "medicine_name": batch.medicine_name,
        "batch_number": batch.batch_number,
        "supplier_id": batch.supplier_id,
        "qr_payload": batch.qr_payload,
        "ocr_extracted_text": batch.ocr_extracted_text,
        "ocr_qr_match_score": batch.ocr_qr_match_score,
        "manufacture_date": batch.manufacture_date.isoformat() if batch.manufacture_date else None,
        "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else None,
        "received_timestamp": batch.received_timestamp.isoformat(),
        "storage_temp_log": [e.model_dump(mode="json") for e in batch.storage_temp_log],
        "physical_inspection_notes": batch.physical_inspection_notes,
        "status": batch.status.value,
    }


@router.post("/scan")
async def post_scan(
    file: UploadFile = File(...),
    supplier_id: str | None = Form(default=None),
    temperature_csv: str | None = Form(default=None),
    physical_inspection_notes: str | None = Form(default=None),
    db: sqlite3.Connection = Depends(_db_dependency),
):
    raw_bytes = await file.read()
    try:
        batch, diagnostics = scan_batch(
            db,
            filename=file.filename,
            content_type=file.content_type,
            raw_bytes=raw_bytes,
            supplier_id_hint=supplier_id,
            temperature_csv=temperature_csv,
            physical_inspection_notes=physical_inspection_notes,
        )
    except InvalidImageIntakeError:
        raise HTTPException(status_code=422, detail="Invalid image")

    response = _batch_to_response(batch)
    if batch.status.value == "MANUAL_REVIEW":
        response["manual_review_reasons"] = diagnostics.get("manual_review_reasons", [])
    return response


@router.get("/batches/{batch_id}")
def get_batch_endpoint(batch_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        batch = get_batch_or_raise(db, batch_id)
    except BatchNotFoundError:
        raise HTTPException(status_code=404, detail="Batch not found")
    return _batch_to_response(batch)


@router.post("/batches/{batch_id}/finalize")
def post_finalize(batch_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        batch, already_finalized = finalize_batch(db, batch_id)
    except BatchNotFoundError:
        raise HTTPException(status_code=404, detail="Batch not found")
    except ManualReviewFinalizeError:
        raise HTTPException(status_code=422, detail="Batch requires manual review before it can be finalized")

    response = _batch_to_response(batch)
    response["already_finalized"] = already_finalized
    return response
