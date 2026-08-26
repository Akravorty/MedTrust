from .conftest import make_blank_image, make_label_image_with_qr


def test_same_image_submitted_twice_reuses_batch(app_client):
    raw = make_label_image_with_qr(batch_number="DUPTEST1")
    r1 = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    r2 = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    assert r1.json()["batch_id"] == r2.json()["batch_id"]


def test_different_images_get_different_batches(app_client):
    raw1 = make_label_image_with_qr(batch_number="UNIQUE1")
    raw2 = make_label_image_with_qr(batch_number="UNIQUE2")
    r1 = app_client.post("/intake/scan", files={"file": ("a.png", raw1, "image/png")})
    r2 = app_client.post("/intake/scan", files={"file": ("b.png", raw2, "image/png")})
    assert r1.json()["batch_id"] != r2.json()["batch_id"]


def test_duplicate_of_manual_review_image_also_reused(app_client):
    raw = make_blank_image(value=255)
    r1 = app_client.post("/intake/scan", files={"file": ("blank.png", raw, "image/png")})
    r2 = app_client.post("/intake/scan", files={"file": ("blank.png", raw, "image/png")})
    assert r1.json()["batch_id"] == r2.json()["batch_id"]