"""
services/intake/temperature.py

Temperature-log ingestion and validation. Intake owns evidence
ingestion/validation ONLY — it never decides whether an excursion makes
a medicine unsafe (that's the Risk Engine's job, via
temp_log_deviation_count as a feature).

Distinguishes:
  - temperature evidence present (valid rows found)
  - temperature evidence missing (no rows at all)
  - temperature evidence malformed (rows present but unusable)

Valid rows are preserved and sorted chronologically even if some rows in
the same submission were invalid; invalid rows are never silently
discarded without a record of what was wrong with them.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from shared.schemas import TempLogEntry

_TIMESTAMP_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
]


class TemperatureValidationResult:
    def __init__(self, valid_entries: list[TempLogEntry], invalid_rows: list[dict], duplicate_count: int):
        self.valid_entries = valid_entries
        self.invalid_rows = invalid_rows
        self.duplicate_count = duplicate_count

    @property
    def status(self) -> str:
        if not self.valid_entries and not self.invalid_rows:
            return "missing"
        if not self.valid_entries and self.invalid_rows:
            return "malformed"
        if self.invalid_rows or self.duplicate_count:
            return "partial"
        return "present"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "valid_count": len(self.valid_entries),
            "invalid_count": len(self.invalid_rows),
            "duplicate_count": self.duplicate_count,
            "invalid_rows": self.invalid_rows,
        }


def _parse_timestamp(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    # Last resort: ISO 8601 general parser
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def validate_temperature_csv(csv_text: str | None) -> TemperatureValidationResult:
    """
    Expects a CSV with headers `timestamp,temp_c` (case-insensitive).
    Returns valid entries sorted chronologically, plus a record of every
    invalid row (with a reason) rather than silently dropping them.
    """
    if csv_text is None or not csv_text.strip():
        return TemperatureValidationResult(valid_entries=[], invalid_rows=[], duplicate_count=0)

    valid_entries: list[TempLogEntry] = []
    invalid_rows: list[dict] = []
    seen_timestamps: set[str] = set()
    duplicate_count = 0

    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]
    except Exception:
        return TemperatureValidationResult(valid_entries=[], invalid_rows=[{"row": None, "reason": "unparseable_csv"}], duplicate_count=0)

    if "timestamp" not in fieldnames or "temp_c" not in fieldnames:
        return TemperatureValidationResult(
            valid_entries=[], invalid_rows=[{"row": None, "reason": "missing_required_columns"}], duplicate_count=0
        )

    for i, row in enumerate(reader):
        # DictReader keys follow original header casing; normalize access.
        norm_row = {k.strip().lower(): v for k, v in row.items() if k is not None}
        ts_raw = (norm_row.get("timestamp") or "").strip()
        temp_raw = (norm_row.get("temp_c") or "").strip()

        if not ts_raw or not temp_raw:
            invalid_rows.append({"row": i, "reason": "missing_field", "raw": dict(row)})
            continue

        ts = _parse_timestamp(ts_raw)
        if ts is None:
            invalid_rows.append({"row": i, "reason": "invalid_timestamp", "raw": dict(row)})
            continue

        try:
            temp_c = float(temp_raw)
        except ValueError:
            invalid_rows.append({"row": i, "reason": "non_numeric_temperature", "raw": dict(row)})
            continue

        ts_key = ts.isoformat()
        if ts_key in seen_timestamps:
            duplicate_count += 1
            continue  # keep the first occurrence, count the rest as duplicates
        seen_timestamps.add(ts_key)

        valid_entries.append(TempLogEntry(timestamp=ts, temp_c=temp_c))

    valid_entries.sort(key=lambda e: e.timestamp)

    return TemperatureValidationResult(valid_entries=valid_entries, invalid_rows=invalid_rows, duplicate_count=duplicate_count)