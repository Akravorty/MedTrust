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
import numpy as np
import cv2

QR_KNOWN_FIELDS = {"batch_number", "medicine_name", "supplier_id", "expiry_date", "manufacture_date"}

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

    for parser in (_try_parse_json, _try_parse_kv_pairs):
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
