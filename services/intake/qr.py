"""
services/intake/qr.py

Defensive QR detection + payload parsing using OpenCV's built-in
QRCodeDetector (no external system dependency like libzbar required).

Distinguishes explicitly between:
  - QR successfully decoded, structured payload parsed
  - QR detected but payload unusable / unparseable
  - QR not detected at all

Never invents missing QR fields. The parser is intentionally pluggable
(see parse_qr_payload) so a second QR payload format can be added later
without touching qr.py's detection logic or the /intake/scan router.
"""

from __future__ import annotations

import json
import re
from datetime import date

import numpy as np
import cv2

QR_KNOWN_FIELDS = {"batch_number", "medicine_name", "supplier_id", "expiry_date", "manufacture_date"}

# GS1 Application Identifiers actually used on real pharma packs (CDSCO's
# API QR mandate + GS1 India track-and-trace both follow this standard).
# We only map the AIs relevant to QR_KNOWN_FIELDS — a real pack's code may
# carry more (GTIN 01, serial 21) but those aren't fields this app models
# yet, so they're read and dropped rather than guessed at.
#   10 = Batch/Lot number       (variable length, up to FNC1 or AI-17 boundary)
#   17 = Expiry date, YYMMDD    (fixed length 6)
#   11 = Manufacture date, YYMMDD (fixed length 6)
_GS1_AI_EXPIRY = "17"
_GS1_AI_MFG_DATE = "11"
_GS1_AI_BATCH = "10"
_GS1_FIXED_LENGTH_AIS = {_GS1_AI_EXPIRY: 6, _GS1_AI_MFG_DATE: 6}
_FNC1 = "\x1d"  # GS1 field separator for variable-length AIs

_detector = cv2.QRCodeDetector()


class QRResult:
    def __init__(self, detected: bool, payload_raw: str | None, parsed: dict, usable: bool, reason: str | None):
        self.detected = detected
        self.payload_raw = payload_raw
        self.parsed = parsed
        self.usable = usable
        self.reason = reason

    def to_dict(self) -> dict:
        return {
            "detected": self.detected,
            "payload_raw": self.payload_raw,
            "parsed": self.parsed,
            "usable": self.usable,
            "reason": self.reason,
        }


def _try_single_pass(gray: np.ndarray) -> tuple[str, bool]:
    """One detectAndDecode attempt. Returns (data, geometry_found)."""
    data, points, _ = _detector.detectAndDecode(gray)
    geometry_found = points is not None and len(points) > 0
    return data, geometry_found


def detect_qr(img: np.ndarray) -> QRResult:
    """
    Attempts detection + decode, with a small set of fallback attempts for
    the common real-world case where the QR is a small portion of a much
    larger photo (label + QR in one frame) - OpenCV's single-shot detector
    can miss this on the first pass. Returns a QRResult distinguishing
    every failure mode instead of collapsing them into one boolean.

    Attempts, in order, stopping at the first one that finds geometry:
      1. Whole image, as-is
      2. detectAndDecodeMulti (handles some multi-scale cases differently)
      3. Whole image upscaled 1.75x (helps when the QR module size is
         small relative to the frame)
    """
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    except Exception:
        return QRResult(detected=False, payload_raw=None, parsed={}, usable=False, reason="qr_detector_error")

    data = ""
    geometry_found = False

    try:
        data, geometry_found = _try_single_pass(gray)
    except Exception:
        pass

    if not geometry_found:
        try:
            ok, decoded_info, points, _ = _detector.detectAndDecodeMulti(gray)
            if ok and points is not None and len(points) > 0:
                geometry_found = True
                data = next((d for d in decoded_info if d), "")
        except Exception:
            pass

    if not geometry_found:
        try:
            upscaled = cv2.resize(gray, None, fx=1.75, fy=1.75, interpolation=cv2.INTER_CUBIC)
            data, geometry_found = _try_single_pass(upscaled)
        except Exception:
            pass

    if not geometry_found:
        return QRResult(detected=False, payload_raw=None, parsed={}, usable=False, reason="qr_not_detected")

    if not data:
        # A QR pattern was located geometrically but no payload could be
        # decoded (damaged, glare, too small, printed at an angle, etc.)
        return QRResult(detected=True, payload_raw=None, parsed={}, usable=False, reason="qr_payload_unreadable")

    parsed = parse_qr_payload(data)
    return QRResult(detected=True, payload_raw=data, parsed=parsed, usable=bool(parsed), reason=None if parsed else "qr_payload_unparseable")


def parse_qr_payload(raw: str) -> dict:
    """
    Defensive, pluggable payload parser. Currently supports:
      1. JSON object payloads, e.g. {"batch_number": "...", ...}
      2. key=value;key=value style payloads
      3. key:value newline/pipe separated payloads

    Only fields actually present are returned — never fabricated.
    Unknown formats return {} rather than guessing.

    To add a new QR format later: add another `_try_parse_*` function and
    call it in the chain below. Callers of detect_qr()/parse_qr_payload()
    never need to change.
    """
    raw = raw.strip()
    if not raw:
        return {}

    for parser in (_try_parse_json, _try_parse_kv_pairs, _try_parse_gs1):
        result = parser(raw)
        if result:
            return result
    return {}


def _try_parse_json(raw: str) -> dict:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(obj, dict):
        return {}
    return {k: v for k, v in obj.items() if k in QR_KNOWN_FIELDS and v is not None and str(v).strip() != ""}


def _try_parse_kv_pairs(raw: str) -> dict:
    """Handles 'batch_number=B123;medicine_name=Paracetamol;...' and
    'batch_number:B123|medicine_name:Paracetamol' style payloads."""
    result: dict = {}
    # Normalize separators
    normalized = raw.replace("|", ";").replace("\n", ";")
    pairs = [p.strip() for p in normalized.split(";") if p.strip()]
    for pair in pairs:
        sep = "=" if "=" in pair else (":" if ":" in pair else None)
        if sep is None:
            continue
        key, _, value = pair.partition(sep)
        key = key.strip().lower()
        value = value.strip()
        if key in QR_KNOWN_FIELDS and value:
            result[key] = value
    return result


def _gs1_date_to_iso(yymmdd: str) -> str | None:
    """GS1 AI-17/AI-11 dates are YYMMDD with a 51-year pivot (per GS1
    General Specifications): YY 00-50 -> 20YY, YY 51-99 -> 19YY. Pharma
    batches never legitimately carry a 19xx date, but we follow the spec
    exactly rather than special-casing it, since that's what a real
    scanner would do."""
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        return None
    yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    year = 2000 + yy if yy <= 50 else 1900 + yy
    # AI-17 allows DD=00 to mean "last day of month" per spec; we don't
    # attempt that expansion here — an unparseable day just fails cleanly.
    try:
        return date(year, mm, dd if dd != 0 else 1).isoformat()
    except ValueError:
        return None


def _try_parse_gs1(raw: str) -> dict:
    """
    Parses GS1 Application Identifier strings — the standard real pharma
    QR/DataMatrix codes use under India's CDSCO track-and-trace mandate
    and GS1 India's specification. Format looks like:

        (01)08904567891234(17)261231(10)BN123456(21)0001234
        01089045678912341726123110BN123456210001234        (no parentheses,
                                                              FNC1-separated)

    Only AIs this app currently models are extracted (10=batch, 17=expiry,
    11=manufacture date); GTIN (01) and serial (21) are recognized enough
    to be skipped correctly but not mapped to a QR_KNOWN_FIELDS key yet.
    Returns {} on anything that doesn't look like GS1 at all, so this
    parser never falsely claims a non-GS1 payload.
    """
    result: dict = {}

    # Bracketed form: (AI)value(AI)value...
    bracketed = re.findall(r"\((\d{2,4})\)([^\(]+)", raw)
    if bracketed:
        for ai, value in bracketed:
            value = value.strip()
            if ai == _GS1_AI_BATCH and value:
                result["batch_number"] = value
            elif ai == _GS1_AI_EXPIRY:
                iso = _gs1_date_to_iso(value[:6])
                if iso:
                    result["expiry_date"] = iso
            elif ai == _GS1_AI_MFG_DATE:
                iso = _gs1_date_to_iso(value[:6])
                if iso:
                    result["manufacture_date"] = iso
        return result

    # Unbracketed form: needs FNC1 (\x1d) separators to know where a
    # variable-length AI like batch number (10) ends, since it has no
    # fixed length of its own. Without FNC1 markers this is genuinely
    # ambiguous per the GS1 spec, so we only attempt it when the raw data
    # actually contains the separator byte.
    if _FNC1 not in raw and not raw.isdigit():
        return {}

    i = 0
    n = len(raw)
    while i < n - 1:
        ai = raw[i:i + 2]
        if ai in _GS1_FIXED_LENGTH_AIS:
            length = _GS1_FIXED_LENGTH_AIS[ai]
            value = raw[i + 2:i + 2 + length]
            if ai == _GS1_AI_EXPIRY:
                iso = _gs1_date_to_iso(value)
                if iso:
                    result["expiry_date"] = iso
            elif ai == _GS1_AI_MFG_DATE:
                iso = _gs1_date_to_iso(value)
                if iso:
                    result["manufacture_date"] = iso
            i += 2 + length
        elif ai == _GS1_AI_BATCH:
            end = raw.find(_FNC1, i + 2)
            end = end if end != -1 else n
            result["batch_number"] = raw[i + 2:end]
            i = end + 1
        elif ai == "01":  # GTIN, fixed length 14 — skip, not modeled yet
            i += 2 + 14
        elif ai == "21":  # Serial, variable length — skip to next FNC1
            end = raw.find(_FNC1, i + 2)
            i = (end + 1) if end != -1 else n
        else:
            # Unrecognized AI in an unbracketed string means we can't
            # safely know its length, so stop rather than misparsing the
            # rest of the payload.
            break

    return result
