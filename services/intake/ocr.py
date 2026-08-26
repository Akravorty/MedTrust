from __future__ import annotations
import os
import shutil
import re
import numpy as np
import cv2

try:
    import pytesseract
    # Windows often doesn't have tesseract on PATH — point to it explicitly if found.
    _tess_cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not _tess_cmd:
        _default_win_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.path.exists(_default_win_path):
            _tess_cmd = _default_win_path
    if _tess_cmd:
        pytesseract.pytesseract.tesseract_cmd = _tess_cmd
except ImportError:  # pragma: no cover
    pytesseract = None

from services.intake.normalization import (
    normalize_batch_number,
    normalize_date,
    normalize_medicine_name,
)

PATTERNS = {
    "batch_number": re.compile(r"(?i)\bbatch\s*(?:no\.?|number)?\s*[:#\-]?\s*([A-Za-z0-9\-]+)"),
    "manufacture_date": re.compile(r"(?i)\b(?:mfg|manufactur\w*)\s*(?:date)?\s*[:#\-]?\s*([0-9\/\-\.]+)"),
    "expiry_date": re.compile(r"(?i)\b(?:exp|expiry)\w*\s*(?:date)?\s*[:#\-]?\s*([0-9\/\-\.]+)"),
}


class OCRResult:
    def __init__(self, raw_text: str, fields: dict, variant_used: str, attempted_variants: list):
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
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2
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
        return ""


def _extract_fields_from_text(text: str) -> dict:
    fields = {"medicine_name": None, "batch_number": None, "manufacture_date": None, "expiry_date": None}

    if not text:
        return fields

    m = PATTERNS["batch_number"].search(text)
    if m:
        fields["batch_number"] = normalize_batch_number(m.group(1))

    m = PATTERNS["manufacture_date"].search(text)
    if m:
        fields["manufacture_date"] = normalize_date(m.group(1))

    m = PATTERNS["expiry_date"].search(text)
    if m:
        fields["expiry_date"] = normalize_date(m.group(1))

    for line in text.splitlines():
        candidate = line.strip()
        letters = sum(c.isalpha() for c in candidate)
        if len(candidate) >= 3 and letters >= max(3, int(len(candidate) * 0.4)):
            fields["medicine_name"] = normalize_medicine_name(candidate)
            break

    return fields


def ocr(img: np.ndarray) -> OCRResult:
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
            best_result = OCRResult(raw_text=text, fields=fields, variant_used=name, attempted_variants=attempted)
            best_field_count = field_count

        if fields.get("batch_number") and field_count >= 2:
            break

    if best_result is None:
        best_result = OCRResult(
            raw_text="",
            fields={"medicine_name": None, "batch_number": None, "manufacture_date": None, "expiry_date": None},
            variant_used="",
            attempted_variants=attempted,
        )

    return best_result


def run_ocr(img: np.ndarray) -> OCRResult:
    """Alias for ocr function to satisfy test suite imports."""
    return ocr(img)


def extract_ocr_data(image_path_or_array):
    """Wrapper function for test suite assertions and OCR processing."""
    if image_path_or_array is None:
        return {
            "medicine_name": None,
            "batch_number": None,
            "manufacture_date": None,
            "expiry": None,
            "expiry_date": None,
            "status": "MANUAL_REVIEW",
        }

    try:
        res = ocr(image_path_or_array)
        fields = res.to_dict()["fields"]
        fields["raw_text"] = res.raw_text
        fields["expiry"] = fields.get("expiry_date")
        fields["status"] = "MANUAL_REVIEW" if not fields.get("batch_number") else "APPROVED"
        return fields
    except Exception:
        return {
            "medicine_name": None,
            "batch_number": None,
            "manufacture_date": None,
            "expiry": None,
            "expiry_date": None,
            "status": "MANUAL_REVIEW",
        }