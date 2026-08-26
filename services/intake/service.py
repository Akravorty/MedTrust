"""
services/intake/service.py

Core orchestration for the Intake pipeline:

  Input Validation -> Image Quality -> QR Detection -> OCR ->
  Field Normalization -> OCR<->QR Identity Verification ->
  Temperature Validation -> Evidence Confidence -> Batch Assembly ->
  Persistence -> Ledger Creation Event

Named errors only ever reach the router - no raw exceptions escape this
module in normal operation. MANUAL_REVIEW is a first-class safety
outcome, never a generic error path.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from shared.schemas import Batch, BatchStatus

from services.intake import storage
from services.intake.validators import InvalidImageError, decode_image, validate_upload_metadata
from services.intake.image_quality import assess_image_quality
from services.intake.qr import detect_qr
from services.intake.ocr import run_ocr
from services.intake.normalization import normalize_batch_number, normalize_medicine_name, normalize_date
from services.intake.identity import compare_identity
from services.intake.temperature import validate_temperature_csv
from services.intake.confidence import assess_evidence_confidence, EvidenceConfidence
from services.intake.fingerprint import compute_fingerprint
from services.intake.ledger_client import send_ledger_event

logger = logging.getLogger("intake.service")


# ---------------------------------------------------------------- errors --

class IntakeError(Exception):
    """Base for all named Intake errors - router catches this, never raw exceptions."""


class InvalidImageIntakeError(IntakeError):
    def __init__(self):
        super().__init__("Invalid image")


class BatchNotFoundError(IntakeError):
    def __init__(self):
        super().__init__("Batch not found")


class OCRExtractionFailedError(IntakeError):
    def __init__(self):
        super().__init__("OCR extraction failed")


class QRNotDetectedError(IntakeError):
    def __init__(self):
        super().__init__("QR not detected")


class AlreadyFinalizedError(IntakeError):
    """Not a failure - finalize is idempotent. Router returns existing state."""


class ManualReviewFinalizeError(IntakeError):
    def __init__(self):
        super().__init__("Batch requires manual review before it can be finalized")


# --------------------------------------------------------------- schema --

def ensure_intake_schema(conn: sqlite3.Connection) -> None:
    storage.ensure_schema(conn)


# ------------------------------------------------------------- scanning --

def scan_batch(
    conn: sqlite3.Connection,
    *,
    filename: str | None,
    content_type: str | None,
    raw_bytes: bytes,
    supplier_id_hint: str | None,
    temperature_csv: str | None,
    physical_inspection_notes: str | None,
) -> tuple[Batch, dict]:
    """
    Runs the full intake pipeline and persists a Batch. Always returns a
    Batch (PENDING or MANUAL_REVIEW) rather than raising, EXCEPT for
    'Invalid image' - a genuinely undecodable/malformed upload has no
    evidence at all to assemble a Batch around, so that alone remains a
    hard error per Section 5's named-error contract.

    Returns (batch, diagnostics) - diagnostics is the internal
    developer-diagnostic payload (Section 22), never exposed on the
    frozen API response unless the caller (router) chooses to.
    """
    logger.info("scan_started filename=%s", filename)

    # 1. Input validation -------------------------------------------------
    try:
        validate_upload_metadata(filename, content_type, len(raw_bytes))
        img = decode_image(raw_bytes)
    except InvalidImageError:
        logger.info("image_validation result=invalid")
        raise InvalidImageIntakeError()
    logger.info("image_validation result=ok")

    fingerprint = compute_fingerprint(raw_bytes)

    # Duplicate/replay protection (Section 15) - reuse existing batch
    # rather than silently creating a duplicate record for the exact same
    # upload.
    existing_row = storage.find_batch_by_fingerprint(conn, fingerprint)
    if existing_row is not None:
        logger.info("duplicate_detected batch_id=%s", existing_row["batch_id"])
        return storage.get_batch(conn, existing_row["batch_id"]), {"duplicate": True, "reused_batch_id": existing_row["batch_id"]}

    # 2. Image quality ------------------------------------------------------
    quality = assess_image_quality(img)
    logger.info("image_quality usable=%s flags=%s", quality["usable"], quality["quality_flags"])

    # 3. QR detection ---------------------------------------------------------
    qr_result = detect_qr(img)
    logger.info("qr_detection detected=%s usable=%s reason=%s", qr_result.detected, qr_result.usable, qr_result.reason)

    # 4. OCR --------------------------------------------------------------------
    if quality["usable"]:
        ocr_result = run_ocr(img)
    else:
        # Don't fabricate OCR on unusable image quality - attempt is skipped
        # entirely rather than pretending a low-quality pass is meaningful.
        from services.intake.ocr import OCRResult
        ocr_result = OCRResult(raw_text="", fields={"medicine_name": None, "batch_number": None, "manufacture_date": None, "expiry_date": None}, variant_used=None, attempted_variants=[])
    logger.info("ocr_extraction field_count=%d variant=%s", sum(v is not None for v in ocr_result.fields.values()), ocr_result.variant_used)

    # 5. Field normalization (OCR fields already normalized in ocr.py;
    #    normalize QR fields here for consistent comparison) -----------------
    qr_fields_normalized = {
        "batch_number": normalize_batch_number(qr_result.parsed.get("batch_number")),
        "medicine_name": normalize_medicine_name(qr_result.parsed.get("medicine_name")),
        "expiry_date": normalize_date(qr_result.parsed.get("expiry_date")) if qr_result.parsed.get("expiry_date") else None,
        "supplier_id": qr_result.parsed.get("supplier_id"),
    }
    logger.info("field_normalization ok")

    # 6. OCR <-> QR identity verification -----------------------------------
    identity_band = None
    identity_result = None
    if any(v is not None for v in ocr_result.fields.values()) and any(v is not None for v in qr_fields_normalized.values()):
        identity_result = compare_identity(ocr_result.fields, qr_fields_normalized)
        identity_band = identity_result.band()
    logger.info("identity_comparison band=%s", identity_band)

    # 7. Temperature validation -------------------------------------------------
    temp_result = validate_temperature_csv(temperature_csv)
    logger.info("temperature_validation status=%s valid=%d invalid=%d", temp_result.status, len(temp_result.valid_entries), len(temp_result.invalid_rows))

    # 8. Evidence confidence -----------------------------------------------------
    expiry_date = ocr_result.fields.get("expiry_date") or qr_fields_normalized.get("expiry_date")
    confidence, reasons = assess_evidence_confidence(
        image_usable=quality["usable"],
        qr_detected=qr_result.detected,
        qr_usable=qr_result.usable,
        ocr_field_count=sum(v is not None for v in ocr_result.fields.values()),
        identity_band=identity_band,
        expiry_date_parsed=expiry_date is not None,
        temperature_status=temp_result.status,
    )

    status = BatchStatus.MANUAL_REVIEW if confidence == EvidenceConfidence.LOW_CONFIDENCE else BatchStatus.PENDING
    if status == BatchStatus.MANUAL_REVIEW:
        logger.info("manual_review reasons=%s", reasons)

    # 9. Batch assembly - preserve every piece of trustworthy evidence that
    #    WAS successfully extracted, never fabricate what wasn't -----------
    medicine_name = ocr_result.fields.get("medicine_name") or qr_fields_normalized.get("medicine_name") or "UNKNOWN"
    batch_number = ocr_result.fields.get("batch_number") or qr_fields_normalized.get("batch_number") or "UNKNOWN"
    supplier_id = supplier_id_hint or qr_fields_normalized.get("supplier_id") or "UNKNOWN"

    batch = Batch(
        batch_id=str(uuid.uuid4()),
        medicine_name=medicine_name,
        batch_number=batch_number,
        supplier_id=supplier_id,
        qr_payload=qr_result.payload_raw,
        ocr_extracted_text={"raw_text": ocr_result.raw_text, "fields": _jsonable(ocr_result.fields)},
        ocr_qr_match_score=identity_result.overall_score if identity_result else None,
        manufacture_date=ocr_result.fields.get("manufacture_date") or qr_fields_normalized.get("manufacture_date"),
        expiry_date=expiry_date,
        received_timestamp=datetime.now(timezone.utc),
        storage_temp_log=temp_result.valid_entries,
        physical_inspection_notes=physical_inspection_notes,
        status=status,
    )

    # 10. Persistence -----------------------------------------------------------
    storage.insert_batch(conn, batch, manual_review_reasons=reasons if status == BatchStatus.MANUAL_REVIEW else [], image_fingerprint=fingerprint)
    logger.info("batch_created batch_id=%s status=%s", batch.batch_id, batch.status.value)

    # 11. Ledger creation event ---------------------------------------------------
    ledger_ok = send_ledger_event(
        conn,
        batch_id=batch.batch_id,
        actor="intake_service",
        action="BATCH_CREATED",
        payload={"status": batch.status.value, "confidence": confidence.value},
    )

    diagnostics = {
        "image_quality": quality,
        "qr": qr_result.to_dict(),
        "ocr": ocr_result.to_dict(),
        "identity": identity_result.to_dict() if identity_result else None,
        "temperature": temp_result.to_dict(),
        "evidence_confidence": confidence.value,
        "manual_review_reasons": reasons if status == BatchStatus.MANUAL_REVIEW else [],
        "ledger_event_sent": ledger_ok,
    }

    return batch, diagnostics


def _jsonable(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


# ------------------------------------------------------------- retrieval --

def get_batch_or_raise(conn: sqlite3.Connection, batch_id: str) -> Batch:
    batch = storage.get_batch(conn, batch_id)
    if batch is None:
        raise BatchNotFoundError()
    return batch


# -------------------------------------------------------------- finalize --

def finalize_batch(conn: sqlite3.Connection, batch_id: str) -> tuple[Batch, bool]:
    """
    Idempotent finalize (Section 16). Returns (batch, was_already_finalized).

    - Batch does not exist -> BatchNotFoundError ("Batch not found")
    - Already finalized -> returns existing state, no duplicate ledger event
    - MANUAL_REVIEW -> ManualReviewFinalizeError; manual review can never
      silently become a successful verification
    """
    row = storage.get_batch_row(conn, batch_id)
    if row is None:
        raise BatchNotFoundError()

    batch = storage.get_batch(conn, batch_id)

    if row["finalized"]:
        logger.info("finalize_skipped_already_finalized batch_id=%s", batch_id)
        return batch, True

    if batch.status == BatchStatus.MANUAL_REVIEW:
        raise ManualReviewFinalizeError()

    storage.mark_finalized(conn, batch_id)
    send_ledger_event(
        conn,
        batch_id=batch_id,
        actor="intake_service",
        action="BATCH_FINALIZED",
        payload={"status": batch.status.value},
    )
    logger.info("batch_finalized batch_id=%s", batch_id)
    return batch, False
