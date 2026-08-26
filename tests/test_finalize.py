from .conftest import make_label_image_with_qr, make_blank_image


def test_finalize_valid_batch(app_client):
    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]

    r = app_client.post(f"/intake/batches/{batch_id}/finalize")
    assert r.status_code == 200
    assert r.json()["already_finalized"] is False


def test_finalize_missing_batch_returns_named_error(app_client):
    r = app_client.post("/intake/batches/nonexistent-id/finalize")
    assert r.status_code == 404
    assert r.json()["detail"] == "Batch not found"


def test_finalize_already_finalized_is_idempotent(app_client):
    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]

    r1 = app_client.post(f"/intake/batches/{batch_id}/finalize")
    r2 = app_client.post(f"/intake/batches/{batch_id}/finalize")
    assert r1.json()["already_finalized"] is False
    assert r2.json()["already_finalized"] is True
    assert r1.json()["status"] == r2.json()["status"]


def test_finalize_manual_review_batch_rejected(app_client):
    raw = make_blank_image(value=255)
    scan = app_client.post("/intake/scan", files={"file": ("blank.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]
    assert scan.json()["status"] == "MANUAL_REVIEW"

    r = app_client.post(f"/intake/batches/{batch_id}/finalize")
    assert r.status_code == 422
    assert "manual review" in r.json()["detail"].lower()