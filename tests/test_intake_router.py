"""
API-level tests for the frozen Intake contract, plus adversarial cases:
random-noise image, rotated/cropped label, glare simulation, garbage
uploads, and the QR-only / OCR-only cases.
"""

import numpy as np
import cv2

from .conftest import make_blank_image, make_label_image_with_qr


def test_scan_returns_batch_shape(app_client):
    raw = make_label_image_with_qr()
    r = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    assert r.status_code == 200
    body = r.json()
    for key in ("batch_id", "medicine_name", "batch_number", "supplier_id", "status"):
        assert key in body


def test_invalid_image_named_error(app_client):
    r = app_client.post("/intake/scan", files={"file": ("junk.png", b"not an image", "image/png")})
    assert r.status_code == 422
    assert r.json()["detail"] == "Invalid image"


def test_unsupported_file_type_rejected(app_client):
    r = app_client.post("/intake/scan", files={"file": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")})
    assert r.status_code == 422
    assert r.json()["detail"] == "Invalid image"


def test_get_batch_not_found(app_client):
    r = app_client.get("/intake/batches/does-not-exist")
    assert r.status_code == 404
    assert r.json()["detail"] == "Batch not found"


def test_random_noise_photo_fails_safely(app_client):
    noise = (np.random.rand(300, 300, 3) * 255).astype(np.uint8)
    ok, buf = cv2.imencode(".png", noise)
    r = app_client.post("/intake/scan", files={"file": ("noise.png", buf.tobytes(), "image/png")})
    # Should never crash - either a valid MANUAL_REVIEW batch or a clean
    # named error, never a 500 with a traceback.
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert r.json()["status"] in ("PENDING", "MANUAL_REVIEW")


def test_rotated_label_does_not_crash(app_client):
    raw = make_label_image_with_qr()
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    center = (img.shape[1] // 2, img.shape[0] // 2)
    matrix = cv2.getRotationMatrix2D(center, 15, 1.0)
    rotated = cv2.warpAffine(img, matrix, (img.shape[1], img.shape[0]), borderValue=(255, 255, 255))
    ok, buf = cv2.imencode(".png", rotated)
    r = app_client.post("/intake/scan", files={"file": ("rotated.png", buf.tobytes(), "image/png")})
    assert r.status_code == 200


def test_blurred_label_falls_back_gracefully(app_client):
    raw = make_label_image_with_qr(blur=True)
    r = app_client.post("/intake/scan", files={"file": ("blurred.png", raw, "image/png")})
    assert r.status_code == 200
    # Blurred heavily enough that this should not silently look PENDING
    # with fabricated data - either MANUAL_REVIEW or genuinely still readable.
    assert r.json()["status"] in ("PENDING", "MANUAL_REVIEW")


def test_cropped_label_missing_expiry_handled(app_client):
    img = np.ones((300, 500, 3), dtype=np.uint8) * 255
    cv2.putText(img, "Paracetamol 500mg", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    cv2.putText(img, "BATCH: B555", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    ok, buf = cv2.imencode(".png", img)
    r = app_client.post("/intake/scan", files={"file": ("cropped.png", buf.tobytes(), "image/png")})
    assert r.status_code == 200
    assert r.json()["expiry_date"] is None  # never fabricated


def test_qr_only_no_ocr_text(app_client):
    import json as _json
    from .conftest import _render_qr
    payload = _json.dumps({"batch_number": "QRONLY1", "medicine_name": "Aspirin"})
    qr_bgr = _render_qr(payload, target_size=200)
    h, w = qr_bgr.shape[:2]
    canvas = np.ones((h + 100, w + 100, 3), dtype=np.uint8) * 255
    canvas[50:50 + h, 50:50 + w] = qr_bgr
    ok, buf = cv2.imencode(".png", canvas)
    r = app_client.post("/intake/scan", files={"file": ("qronly.png", buf.tobytes(), "image/png")})
    assert r.status_code == 200
    assert r.json()["batch_number"] == "QRONLY1"


def test_ocr_only_no_qr(app_client):
    img = np.ones((300, 500, 3), dtype=np.uint8) * 255
    cv2.putText(img, "Paracetamol 500mg", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    cv2.putText(img, "BATCH: B888", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    ok, buf = cv2.imencode(".png", img)
    r = app_client.post("/intake/scan", files={"file": ("ocronly.png", buf.tobytes(), "image/png")})
    assert r.status_code == 200
    assert r.json()["qr_payload"] is None
    assert r.json()["batch_number"] == "B888"


def test_huge_upload_rejected(app_client):
    huge = b"\x00" * (16 * 1024 * 1024)
    r = app_client.post("/intake/scan", files={"file": ("huge.jpg", huge, "image/jpeg")})
    assert r.status_code == 422
    assert r.json()["detail"] == "Invalid image"


def test_no_raw_exception_ever_in_response_body(app_client):
    """Adversarial sweep: none of these should ever leak a traceback."""
    bad_inputs = [
        make_blank_image(255),
        make_blank_image(0),
        b"garbage",
        (np.random.rand(50, 50, 3) * 255).astype(np.uint8).tobytes(),
    ]
    for i, raw in enumerate(bad_inputs):
        r = app_client.post("/intake/scan", files={"file": (f"x{i}.png", raw, "image/png")})
        body_text = r.text.lower()
        assert "traceback" not in body_text
        assert "opencv" not in body_text
        assert "tesseract" not in body_text
        assert "filenotfounderror" not in body_text