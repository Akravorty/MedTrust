"""
services/intake/confidence.py

Internal evidence-confidence assessment (Section 11). This is NOT
Batch.status and NEVER becomes ACCEPT/HOLD/REJECT — that stays the Risk
Engine's job entirely. It only informs whether Intake itself should
progress the batch normally or fall back to MANUAL_REVIEW.
"""

from __future__ import annotations

from enum import Enum


class EvidenceConfidence(str, Enum):
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    MEDIUM_CONFIDENCE = "MEDIUM_CONFIDENCE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


def assess_evidence_confidence(
    *,
    image_usable: bool,
    qr_detected: bool,
    qr_usable: bool,
    ocr_field_count: int,
    identity_band: str | None,   # "strong_match" | "uncertain" | "mismatch" | None (nothing to compare)
    expiry_date_parsed: bool,
    temperature_status: str,     # "present" | "partial" | "missing" | "malformed"
) -> tuple[EvidenceConfidence, list[str]]:
    """
    Returns (confidence_level, reasons). Reasons are human-readable and
    intended to be shown verbatim in a MANUAL_REVIEW response.
    """
    reasons: list[str] = []
    score = 0
    max_score = 6

    if image_usable:
        score += 1
    else:
        reasons.append("Image quality inadequate for reliable extraction")

    if qr_detected and qr_usable:
        score += 1
    elif qr_detected and not qr_usable:
        reasons.append("QR code detected but payload could not be read")
    else:
        reasons.append("QR code could not be reliably detected")

    if ocr_field_count >= 3:
        score += 1
    elif ocr_field_count > 0:
        reasons.append("OCR extraction partially incomplete")
    else:
        reasons.append("OCR extraction failed to find any fields")

    if identity_band == "strong_match":
        score += 1
    elif identity_band == "uncertain":
        reasons.append("OCR and QR identity signals only partially agree")
    elif identity_band == "mismatch":
        reasons.append("OCR and QR identity signals disagree")
    # identity_band is None (nothing to compare) -> neutral, no point either way

    if expiry_date_parsed:
        score += 1
    else:
        reasons.append("Expiry date could not be reliably parsed")

    if temperature_status == "present":
        score += 1
    elif temperature_status == "partial":
        reasons.append("Temperature evidence partially incomplete or malformed")
    elif temperature_status == "malformed":
        reasons.append("Temperature evidence present but malformed")
    else:
        reasons.append("Temperature evidence not provided")

    ratio = score / max_score

    # A hard mismatch always caps confidence at LOW, regardless of how well
    # everything else scored - never let good OCR paper over a real identity
    # conflict.
    if identity_band == "mismatch":
        return EvidenceConfidence.LOW_CONFIDENCE, reasons

    if ratio >= 0.80:
        return EvidenceConfidence.HIGH_CONFIDENCE, reasons
    if ratio >= 0.5:
        return EvidenceConfidence.MEDIUM_CONFIDENCE, reasons
    return EvidenceConfidence.LOW_CONFIDENCE, reasons