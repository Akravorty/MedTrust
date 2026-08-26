import numpy as np
import cv2

from services.intake.ocr import run_ocr


def _label_image(lines, size=(400, 700)):
    img = np.ones((size[0], size[1], 3), dtype=np.uint8) * 255
    y = 60
    for line in lines:
        cv2.putText(img, line, (30, y), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 2)
        y += 70
    return img


def test_clean_label_extracts_fields():
    img = _label_image(["Paracetamol 500mg", "BATCH: B12345", "EXP: 12/2027"])
    result = run_ocr(img)
    assert result.fields["batch_number"] == "B12345"
    assert result.fields["expiry_date"] is not None
    assert result.has_any_field


def test_partial_label_missing_batch_number():
    img = _label_image(["Paracetamol 500mg", "EXP: 12/2027"])
    result = run_ocr(img)
    assert result.fields["batch_number"] is None
    assert result.fields["expiry_date"] is not None


def test_missing_expiry():
    img = _label_image(["Paracetamol 500mg", "BATCH: B999"])
    result = run_ocr(img)
    assert result.fields["batch_number"] == "B999"
    assert result.fields["expiry_date"] is None


def test_unreadable_blank_image_returns_no_fields():
    img = np.ones((300, 300, 3), dtype=np.uint8) * 255
    result = run_ocr(img)
    assert not result.has_any_field
    assert result.fields["batch_number"] is None


def test_noisy_label_still_attempts_extraction_without_crashing():
    img = _label_image(["Paracetamol 500mg", "BATCH: B12345"])
    noise = (np.random.rand(*img.shape) * 40).astype(np.uint8)
    noisy = cv2.add(img, noise)
    result = run_ocr(noisy)  # should not raise regardless of extraction success
    assert isinstance(result.fields, dict)


def test_never_fabricates_missing_field():
    img = _label_image(["Paracetamol 500mg"])
    result = run_ocr(img)
    assert result.fields["batch_number"] is None
    assert result.fields["expiry_date"] is None
    assert result.fields["manufacture_date"] is None