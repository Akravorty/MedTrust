"""
services/intake/fingerprint.py

Content-fingerprint (SHA-256) computation for replay/duplicate-submission
protection (Section 15). This does NOT claim two identical images are
necessarily the same physical batch - it only protects against accidental
double-submission of the exact same upload.
"""

from __future__ import annotations

import hashlib


def compute_fingerprint(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()
