"""
services/intake/diagnostics.py

Section 22 - developer-only diagnostic utility for integration debugging.
NOT another API endpoint (would complicate the frozen contract) - just an
importable function for use in a test/dev script or a REPL.

Usage:
    from services.intake.diagnostics import diagnose_image
    report = diagnose_image(open("sample.jpg", "rb").read())
"""

from __future__ import annotations

import time

from services.intake.validators import decode_image, InvalidImageError
from services.intake.image_quality import assess_image_quality
from services.intake.qr import detect_qr
from services.intake.ocr import run_ocr
from services.intake.normalization import normalize_batch_number, normalize_medicine_name, normalize_date
from services.intake.identity import compare_identity
from services.intake.confidence import assess_evidence_confidence, EvidenceConfidence


def diagnose_image(raw_bytes: bytes) -> dict:
    """
    Runs the pipeline read-only (no DB write, no ledger call) and reports
    a full breakdown of every stage's outcome plus rough timing per stage,
    matching the Section 22 checklist.
    """
    timings: dict[str, float] = {}
    t0 = time.perf_counter()

    try:
        img = decode_image(raw_bytes)
    except InvalidImageError as exc:
        return {"valid_image": False, "reason": str(exc)}
    timings["decode_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    t = time.perf_counter()
    quality = assess_image_quality(img)
    timings["image_quality_ms"] = round((time.perf_counter() - t) * 1000, 2)

    t = time.perf_counter()
    qr_result = detect_qr(img)
    timings["qr_ms"] = round((time.perf_counter() - t) * 1000, 2)

    t = time.perf_counter()
    ocr_result = run_ocr(img) if quality["usable"] else None
    timings["ocr_ms"] = round((time.perf_counter() - t) * 1000, 2)

    qr_fields_normalized = {
        "batch_number": normalize_batch_number(qr_result.parsed.get("batch_number")),
        "medicine_name": normalize_medicine_name(qr_result.parsed.get("medicine_name")),
        "expiry_date": normalize_date(qr_result.parsed.get("expiry_date")) if qr_result.parsed.get("expiry_date") else None,
        "supplier_id": qr_result.parsed.get("supplier_id"),
    }

    t = time.perf_counter()
    identity_result = None
    ocr_fields = ocr_result.fields if ocr_result else {}
    if any(v is not None for v in ocr_fields.values()) and any(v is not None for v in qr_fields_normalized.values()):
        identity_result = compare_identity(ocr_fields, qr_fields_normalized)
    timings["identity_ms"] = round((time.perf_counter() - t) * 1000, 2)

    ocr_field_count = sum(v is not None for v in ocr_fields.values())
    expiry_parsed = bool(ocr_fields.get("expiry_date") or qr_fields_normalized.get("expiry_date"))
    confidence, reasons = assess_evidence_confidence(
        image_usable=quality["usable"],
        qr_detected=qr_result.detected,
        qr_usable=qr_result.usable,
        ocr_field_count=ocr_field_count,
        identity_band=identity_result.band() if identity_result else None,
        expiry_date_parsed=expiry_parsed,
        temperature_status="missing",
    )

    timings["total_ms"] = round(sum(timings.values()), 2)

    return {
        "valid_image": True,
        "image_quality": quality,
        "qr_detected": qr_result.detected,
        "qr_usable": qr_result.usable,
        "ocr_successful": ocr_field_count > 0,
        "required_fields_present": {
            "medicine_name": bool(ocr_fields.get("medicine_name") or qr_fields_normalized.get("medicine_name")),
            "batch_number": bool(ocr_fields.get("batch_number") or qr_fields_normalized.get("batch_number")),
            "expiry_date": expiry_parsed,
        },
        "identity_match": identity_result.to_dict() if identity_result else None,
        "temperature_evidence_valid": None,  # not applicable to a bare image diagnostic
        "manual_review_required": confidence == EvidenceConfidence.LOW_CONFIDENCE,
        "evidence_confidence": confidence.value,
        "reasons": reasons,
        "timings_ms": timings,
    }