"""
services/intake/validators.py

Defensive input validation for uploaded medicine-package images.

Rule: never trust MIME type / extension alone. Always attempt an actual
image decode. Any failure here maps to the named error "Invalid image" —
never a raw exception, traceback, or library-specific error string.
"""

from __future__ import annotations

import numpy as np
import cv2

SUPPORTED_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/bmp"}
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

MIN_FILE_SIZE_BYTES = 512          # anything smaller can't be a real photo
MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024   # 15 MB upload cap
MIN_DIMENSION_PX = 64                     # below this, quality assessment is meaningless


class InvalidImageError(Exception):
    """Raised for any defect in the uploaded image. Router maps this to 'Invalid image'."""


def validate_upload_metadata(filename: str | None, content_type: str | None, size_bytes: int) -> None:
    """Cheap checks before we even touch image bytes."""
    if not filename:
        raise InvalidImageError("missing filename")

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise InvalidImageError(f"unsupported extension: {ext}")

    if content_type is not None and content_type.lower() not in SUPPORTED_CONTENT_TYPES:
        # Not fatal alone (browsers/clients lie about content-type), but combined
        # with a bad extension it's a hard reject. We already rejected bad
        # extension above, so a mismatched-but-plausible content-type is allowed
        # through to real decode, which is the actual source of truth.
        pass

    if size_bytes < MIN_FILE_SIZE_BYTES:
        raise InvalidImageError("file too small")
    if size_bytes > MAX_FILE_SIZE_BYTES:
        raise InvalidImageError("file too large")


def decode_image(raw_bytes: bytes) -> np.ndarray:
    """
    Actually decode the bytes as an image. This is the real defense against
    a malicious/corrupt/mislabeled upload — MIME type is not trusted alone.

    Returns a BGR numpy array (OpenCV convention). Raises InvalidImageError
    on any failure, with no library-specific detail leaked to the caller.
    """
    if not raw_bytes:
        raise InvalidImageError("empty file")

    try:
        arr = np.frombuffer(raw_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, converted to named error
        raise InvalidImageError("decode failure") from exc

    if img is None:
        raise InvalidImageError("could not decode image")

    h, w = img.shape[:2]
    if h < MIN_DIMENSION_PX or w < MIN_DIMENSION_PX:
        raise InvalidImageError("image dimensions too small")

    return img