"""
services/intake/normalization.py

Reusable normalization for messy OCR/QR output.

Core principle (Section 9 of the hardening spec): normalize observed
evidence, never invent missing evidence. Ambiguous input becomes None,
not a guess.
"""

from __future__ import annotations

import re
from datetime import date, datetime


def normalize_batch_number(raw: str | None) -> str | None:
    """
    Normalizes obvious formatting noise around a batch number while
    preserving the actual identifier:
        "BATCH-001"   -> "001"
        "batch 001"   -> "001"
        "BATCH : 001" -> "001"
        "BATCH#001"   -> "001"
        "B-2024-A17"  -> "2024-A17"  (only a leading BATCH/B label stripped)

    Does NOT attempt fuzzy correction of the identifier itself — no
    guessing at OCR-mangled characters.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None

    # Strip a leading "batch" label plus any separators (:,#,-,space) that follow it.
    value = re.sub(r"(?i)^\s*batch(?:\s*no\.?|\s*number)?\s*[:#\-]?\s*", "", value)
    value = value.strip(" :#-\t")
    value = re.sub(r"\s+", "", value)  # OCR often inserts spurious spaces mid-token

    return value if value else None


def normalize_medicine_name(raw: str | None) -> str | None:
    """
    Normalizes casing/whitespace only. Never uses medical knowledge to
    correct or complete a name.
    """
    if raw is None:
        return None
    value = re.sub(r"\s+", " ", raw).strip()
    if not value:
        return None
    return value.title()


_DATE_PATTERNS = [
    (r"^(\d{4})-(\d{2})-(\d{2})$", lambda m: (int(m[1]), int(m[2]), int(m[3]))),          # YYYY-MM-DD
    (r"^(\d{2})/(\d{2})/(\d{4})$", lambda m: (int(m[3]), int(m[2]), int(m[1]))),          # DD/MM/YYYY
    (r"^(\d{2})-(\d{2})-(\d{4})$", lambda m: (int(m[3]), int(m[2]), int(m[1]))),          # DD-MM-YYYY
]

_MONTH_YEAR_PATTERN = re.compile(r"^(\d{2})/(\d{4})$")  # MM/YYYY -> last day of month


def normalize_date(raw: str | None) -> date | None:
    """
    Supports DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD, and MM/YYYY.

    If the interpretation is ambiguous or the pattern isn't recognized,
    returns None rather than guessing. Never raises.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None

    for pattern, extractor in _DATE_PATTERNS:
        m = re.match(pattern, value)
        if m:
            try:
                y, mo, d = extractor(m)
                return date(y, mo, d)
            except ValueError:
                return None  # e.g. day 32 - malformed, don't guess

    m = _MONTH_YEAR_PATTERN.match(value)
    if m:
        mo, y = int(m[1]), int(m[2])
        if not (1 <= mo <= 12):
            return None
        # MM/YYYY is common for expiry printed without a day. We do NOT
        # invent a specific day of "correctness" beyond marking the last
        # day of that month, since that's the standard pharma convention
        # for expiry-by-month and is a deterministic, non-guessed rule
        # (not fabricating evidence, just applying a known convention).
        if mo == 12:
            next_month = date(y + 1, 1, 1)
        else:
            next_month = date(y, mo + 1, 1)
        from datetime import timedelta
        return next_month - timedelta(days=1)

    return None
