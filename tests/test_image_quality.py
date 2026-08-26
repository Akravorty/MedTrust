import numpy as np
import cv2

from services.intake.image_quality import assess_image_quality


def test_good_quality_image_usable():
    img = np.ones((300, 300, 3), dtype=np.uint8) * 200
    cv2.putText(img, "SOME TEXT HERE", (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
    result = assess_image_quality(img)
    assert result["usable"] is True
    assert "excessive_blur" not in result["quality_flags"]


def test_blank_white_image_flagged():
    img = np.ones((300, 300, 3), dtype=np.uint8) * 255
    result = assess_image_quality(img)
    assert "blank_or_empty_image" in result["quality_flags"]
    assert result["usable"] is False


def test_blank_black_image_flagged():
    img = np.zeros((300, 300, 3), dtype=np.uint8)
    result = assess_image_quality(img)
    assert "blank_or_empty_image" in result["quality_flags"] or "image_too_dark" in result["quality_flags"]


def test_dark_image_flagged():
    img = np.ones((300, 300, 3), dtype=np.uint8) * 15
    cv2.rectangle(img, (50, 50), (100, 100), (20, 20, 20), -1)
    result = assess_image_quality(img)
    assert "image_too_dark" in result["quality_flags"]


def test_bright_washed_out_image_flagged():
    img = np.ones((300, 300, 3), dtype=np.uint8) * 250
    result = assess_image_quality(img)
    assert "image_too_bright" in result["quality_flags"]


def test_blurred_image_flagged():
    img = np.ones((300, 300, 3), dtype=np.uint8) * 200
    cv2.putText(img, "SHARP TEXT", (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    blurred = cv2.GaussianBlur(img, (35, 35), 20)
    result = assess_image_quality(blurred)
    assert "excessive_blur" in result["quality_flags"]


def test_low_contrast_flagged():
    img = np.ones((300, 300, 3), dtype=np.uint8) * 128
    noise = (np.random.rand(300, 300, 3) * 4).astype(np.uint8)
    img = cv2.add(img, noise)
    result = assess_image_quality(img)
    assert "low_contrast" in result["quality_flags"]


def test_quality_result_shape():
    img = np.ones((300, 300, 3), dtype=np.uint8) * 200
    result = assess_image_quality(img)
    assert set(result.keys()) == {"usable", "blur_score", "brightness_score", "contrast_score", "quality_flags"}