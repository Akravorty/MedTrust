"""
services/intake/identity.py

Multi-signal OCR <-> QR identity verification (Section 10).

Compares whatever fields are actually available on both sides —
batch_number, medicine_name, expiry_date, supplier_id — with batch_number
weighted most heavily. Returns a structured diagnostic result; the
shared Batch.ocr_qr_match_score stores only the overall_score float.

An exact batch-number mismatch is never hidden behind a decent fuzzy
overall score — it is always surfaced in `mismatches`.
"""

from __future__ import annotations

from datetime import date

from rapidfuzz import fuzz

# Field weights - batch number carries the most identity weight per spec.
_WEIGHTS = {
    "batch_number": 0.5,
    "medicine_name": 0.25,
    "expiry_date": 0.15,
    "supplier_id": 0.10,
}

STRONG_MATCH_THRESHOLD = 0.90
UNCERTAIN_THRESHOLD = 0.70  # >= this and < STRONG -> uncertain/manual review; < this -> mismatch


class IdentityComparisonResult:
    def __init__(self, overall_score: float, per_field: dict, mismatches: list[str], fields_compared: list[str]):
        self.overall_score = overall_score
        self.per_field = per_field
        self.mismatches = mismatches
        self.fields_compared = fields_compared

    def band(self) -> str:
        if self.overall_score >= STRONG_MATCH_THRESHOLD:
            return "strong_match"
        if self.overall_score >= UNCERTAIN_THRESHOLD:
            return "uncertain"
        return "mismatch"

    def to_dict(self) -> dict:
        return {
            "overall_score": round(self.overall_score, 4),
            "batch_number_score": self.per_field.get("batch_number"),
            "medicine_name_score": self.per_field.get("medicine_name"),
            "expiry_date_match": self.per_field.get("expiry_date"),
            "supplier_match": self.per_field.get("supplier_id"),
            "mismatches": self.mismatches,
            "band": self.band(),
        }


def _string_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return fuzz.ratio(a.strip().lower(), b.strip().lower()) / 100.0


def compare_identity(ocr_fields: dict, qr_fields: dict) -> IdentityComparisonResult:
    """
    ocr_fields / qr_fields may each be missing any key. Only fields present
    on BOTH sides are compared and contribute to overall_score; fields
    present on only one side are neither a match nor mismatch (there's
    nothing to cross-check), and don't inflate or deflate the score.
    """
    per_field: dict = {}
    mismatches: list[str] = []
    fields_compared: list[str] = []
    weighted_sum = 0.0
    weight_total = 0.0

    for field in ("batch_number", "medicine_name"):
        ocr_val = ocr_fields.get(field)
        qr_val = qr_fields.get(field)
        if ocr_val is None or qr_val is None:
            continue
        fields_compared.append(field)
        score = _string_similarity(str(ocr_val), str(qr_val))
        per_field[field] = round(score, 4)
        weighted_sum += score * _WEIGHTS[field]
        weight_total += _WEIGHTS[field]
        if field == "batch_number" and score < 0.999:
            # Batch number must match exactly to be trusted - any deviation
            # is a named mismatch regardless of the fuzzy score, so it can
            # never be hidden behind a decent overall_score.
            mismatches.append(f"batch_number_mismatch: ocr='{ocr_val}' qr='{qr_val}' (similarity={score:.2f})")
        elif score < 0.75:
            mismatches.append(f"{field}_mismatch: ocr='{ocr_val}' qr='{qr_val}' (similarity={score:.2f})")

    for field in ("expiry_date",):
        ocr_val = ocr_fields.get(field)
        qr_val = qr_fields.get(field)
        if ocr_val is None or qr_val is None:
            continue
        fields_compared.append(field)
        is_match = _dates_equal(ocr_val, qr_val)
        per_field[field] = is_match
        weighted_sum += (1.0 if is_match else 0.0) * _WEIGHTS[field]
        weight_total += _WEIGHTS[field]
        if not is_match:
            mismatches.append(f"expiry_date_mismatch: ocr='{ocr_val}' qr='{qr_val}'")

    for field in ("supplier_id",):
        ocr_val = ocr_fields.get(field)
        qr_val = qr_fields.get(field)
        if ocr_val is None or qr_val is None:
            continue
        fields_compared.append(field)
        is_match = str(ocr_val).strip().lower() == str(qr_val).strip().lower()
        per_field[field] = is_match
        weighted_sum += (1.0 if is_match else 0.0) * _WEIGHTS[field]
        weight_total += _WEIGHTS[field]
        if not is_match:
            mismatches.append(f"supplier_id_mismatch: ocr='{ocr_val}' qr='{qr_val}'")

    overall_score = (weighted_sum / weight_total) if weight_total > 0 else 0.0

    return IdentityComparisonResult(
        overall_score=overall_score,
        per_field=per_field,
        mismatches=mismatches,
        fields_compared=fields_compared,
    )


def _dates_equal(a, b) -> bool:
    if isinstance(a, date) and isinstance(b, date):
        return a == b
    return str(a) == str(b)