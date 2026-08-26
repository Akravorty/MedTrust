"""
Manual review is a first-class safety outcome, not a generic error
(Section 12). These tests verify: batch is still created where possible,
status is MANUAL_REVIEW, no fabricated values, clear reasons exist, and
no exception ever escapes.
"""

from .conftest import make_blank_image, make_label_image_with_qr


def test_blank_image_triggers_manual_review(app_client):
    raw = make_blank_image(value=255)
    r = app_client.post("/intake/scan", files={"file": ("blank.png", raw, "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "MANUAL_REVIEW"
    assert len(body["manual_review_reasons"]) > 0


def test_manual_review_batch_still_retrievable(app_client):
    raw = make_blank_image(value=0)
    r = app_client.post("/intake/scan", files={"file": ("black.png", raw, "image/png")})
    batch_id = r.json()["batch_id"]
    r2 = app_client.get(f"/intake/batches/{batch_id}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "MANUAL_REVIEW"


def test_manual_review_never_fabricates_batch_number(app_client):
    raw = make_blank_image(value=255)
    r = app_client.post("/intake/scan", files={"file": ("blank.png", raw, "image/png")})
    body = r.json()
    assert body["batch_number"] == "UNKNOWN"  # explicit placeholder, never a guessed value


def test_severe_ocr_qr_mismatch_forces_manual_review(app_client):
    raw = make_label_image_with_qr(batch_number="B123", qr_batch_number="B999")
    r = app_client.post("/intake/scan", files={"file": ("mismatch.png", raw, "image/png")})
    body = r.json()
    # A hard batch-number mismatch caps confidence at LOW -> MANUAL_REVIEW
    assert body["status"] == "MANUAL_REVIEW"
    assert any("mismatch" in reason.lower() or "disagree" in reason.lower() for reason in body["manual_review_reasons"])


def test_good_evidence_does_not_trigger_manual_review(app_client):
    raw = make_label_image_with_qr()
    r = app_client.post("/intake/scan", files={"file": ("good.png", raw, "image/png")})
    body = r.json()
    assert body["status"] == "PENDING"