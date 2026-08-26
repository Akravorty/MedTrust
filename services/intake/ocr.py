"""
services/intake/ocr.py

OCR extraction with a small number of lightweight preprocessing variants,
since a single raw pass is fragile against real-world label photos.

Variants tried (in order, cheapest/most-likely-to-work first):
  1. grayscale
  2. grayscale + contrast enhancement (CLAHE)
  3. grayscale + adaptive threshold (binarization)

We stop as soon as a variant yields usable field extraction, to keep the
pipeline fast (Section 23) — we do not run every variant unconditionally.

Extracts, where possible: medicine_name, batch_number, manufacture_date,
expiry_date. Never invents a field that raw OCR text doesn't support.
"""

from __future__ import annotations

import re
import numpy as np
import cv2

try:
    import pytesseract
except ImportError:  # pragma: no cover - exercised only if tesseract isn't installed
    pytesseract = None

from services.intake.normalization import normalize_batch_number, normalize_medicine_name, normalize_date

_LABEL_PATTERNS = {
    "batch_number": re.compile(r"(?i)\bbatch\s*(?:no\.?|number)?\s*[:#\-]?\s*([A-Za-z0-9\-]{2,20})"),
    "manufacture_date": re.compile(r"(?i)\b(?:mfg|manufactur\w*)\s*(?:date)?\s*[:\-]?\s*([0-9/\-]{4,10})"),
    "expiry_date": re.compile(r"(?i)\b(?:exp|expiry)\w*\s*(?:date)?\s*[:\-]?\s*([0-9/\-]{4,10})"),
}


class OCRResult:
    def __init__(self, raw_text: str, fields: dict, variant_used: str | None, attempted_variants: list[str]):
        self.raw_text = raw_text
        self.fields = fields
        self.variant_used = variant_used
        self.attempted_variants = attempted_variants

    @property
    def has_any_field(self) -> bool:
        return any(v is not None for v in self.fields.values())

    def to_dict(self) -> dict:
        return {
            "raw_text": self.raw_text,
            "fields": self.fields,
            "variant_used": self.variant_used,
            "attempted_variants": self.attempted_variants,
        }


def _preprocess_grayscale(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _preprocess_contrast_enhanced(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _preprocess_thresholded(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
    )


_VARIANTS = [
    ("grayscale", _preprocess_grayscale),
    ("contrast_enhanced", _preprocess_contrast_enhanced),
    ("thresholded", _preprocess_thresholded),
]


def _run_tesseract(processed: np.ndarray) -> str:
    if pytesseract is None:
        return ""
    try:
        return pytesseract.image_to_string(processed)
    except Exception:
        # Tesseract binary missing / misconfigured / internal error -> treat
        # as "no text extracted" rather than leaking a library exception.
        return ""


def _extract_fields_from_text(text: str) -> dict:
    fields: dict = {"medicine_name": None, "batch_number": None, "manufacture_date": None, "expiry_date": None}

    m = _LABEL_PATTERNS["batch_number"].search(text)
    if m:
        fields["batch_number"] = normalize_batch_number(m.group(1))

    m = _LABEL_PATTERNS["manufacture_date"].search(text)
    if m:
        fields["manufacture_date"] = normalize_date(m.group(1))

    m = _LABEL_PATTERNS["expiry_date"].search(text)
    if m:
        fields["expiry_date"] = normalize_date(m.group(1))

    # Medicine name: no reliable label prefix on most labels, so take the
    # first non-empty, reasonably long alphabetic-heavy line as a best
    # observed guess of the printed name -- NOT filled from any external
    # knowledge base, purely from what's visibly printed.
    for line in text.splitlines():
        candidate = line.strip()
        letters = sum(c.isalpha() for c in candidate)
        if len(candidate) >= 3 and letters >= max(3, int(len(candidate) * 0.6)):
            fields["medicine_name"] = normalize_medicine_name(candidate)
            break

    return fields


def run_ocr(img: np.ndarray) -> OCRResult:
    """
    Tries preprocessing variants in order, stopping early once a variant
    yields at least one usable field. Returns the OCRResult from whichever
    variant did best (by field count) if none hit early-stop.
    """
    attempted: list[str] = []
    best_result: OCRResult | None = None
    best_field_count = -1

    for name, preprocess_fn in _VARIANTS:
        attempted.append(name)
        try:
            processed = preprocess_fn(img)
        except Exception:
            continue

        text = _run_tesseract(processed)
        fields = _extract_fields_from_text(text)
        field_count = sum(v is not None for v in fields.values())

        if field_count > best_field_count:
            best_result = OCRResult(raw_text=text, fields=fields, variant_used=name, attempted_variants=list(attempted))
            best_field_count = field_count

        # Early stop: batch_number is the single most valuable field
        # (Section 10 - it carries the strongest identity weight), so if
        # we already have it plus at least one more field, stop spending
        # time on further variants.
        if fields.get("batch_number") and field_count >= 2:
            break

    if best_result is None:
        best_result = OCRResult(raw_text="", fields={"medicine_name": None, "batch_number": None, "manufacture_date": None, "expiry_date": None}, variant_used=None, attempted_variants=attempted)

    return best_result
