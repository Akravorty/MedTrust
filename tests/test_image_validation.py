import pytest
import numpy as np
import cv2

from services.intake.validators import (
    InvalidImageError,
    decode_image,
    validate_upload_metadata,
    MAX_FILE_SIZE_BYTES,
)


def test_valid_image_decodes():
    img = np.ones((200, 200, 3), dtype=np.uint8) * 128
    ok, buf = cv2.imencode(".png", img)
    decoded = decode_image(buf.tobytes())
    assert decoded is not None
    assert decoded.shape[0] >= 64


def test_corrupt_image_raises():
    with pytest.raises(InvalidImageError):
        decode_image(b"this is not an image, just garbage bytes 12345")


def test_empty_bytes_raises():
    with pytest.raises(InvalidImageError):
        decode_image(b"")


def test_very_small_image_raises():
    img = np.ones((10, 10, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    with pytest.raises(InvalidImageError):
        decode_image(buf.tobytes())


def test_unsupported_extension_raises():
    with pytest.raises(InvalidImageError):
        validate_upload_metadata("document.txt", "text/plain", 10_000)


def test_oversized_upload_raises():
    with pytest.raises(InvalidImageError):
        validate_upload_metadata("photo.jpg", "image/jpeg", MAX_FILE_SIZE_BYTES + 1)


def test_undersized_upload_raises():
    with pytest.raises(InvalidImageError):
        validate_upload_metadata("photo.jpg", "image/jpeg", 10)


def test_missing_filename_raises():
    with pytest.raises(InvalidImageError):
        validate_upload_metadata(None, "image/jpeg", 10_000)