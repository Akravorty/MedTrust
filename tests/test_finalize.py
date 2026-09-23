from .conftest import FINALIZE_BODY, accept_batch, make_label_image_with_qr, make_blank_image


def test_finalize_valid_batch(app_client):
    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]
    accept_batch(batch_id)

    r = app_client.post(f"/intake/batches/{batch_id}/finalize", json=FINALIZE_BODY)
    assert r.status_code == 200
    assert r.json()["already_finalized"] is False


def test_finalize_missing_batch_returns_named_error(app_client):
    r = app_client.post("/intake/batches/nonexistent-id/finalize", json=FINALIZE_BODY)
    assert r.status_code == 404
    assert r.json()["detail"] == "Batch not found"


def test_finalize_missing_receiver_identity_is_rejected(app_client):
    """facility_id/received_by/role are required by the request body --
    FastAPI/pydantic should reject an incomplete body before finalize_batch
    ever runs."""
    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]
    accept_batch(batch_id)

    r = app_client.post(f"/intake/batches/{batch_id}/finalize", json={})
    assert r.status_code == 422


def test_finalize_already_finalized_is_idempotent(app_client):
    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]
    accept_batch(batch_id)

    r1 = app_client.post(f"/intake/batches/{batch_id}/finalize", json=FINALIZE_BODY)
    r2 = app_client.post(f"/intake/batches/{batch_id}/finalize", json=FINALIZE_BODY)
    assert r1.json()["already_finalized"] is False
    assert r2.json()["already_finalized"] is True
    assert r1.json()["status"] == r2.json()["status"]


def test_finalize_manual_review_batch_rejected(app_client):
    raw = make_blank_image(value=255)
    scan = app_client.post("/intake/scan", files={"file": ("blank.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]
    assert scan.json()["status"] == "MANUAL_REVIEW"

    r = app_client.post(f"/intake/batches/{batch_id}/finalize", json=FINALIZE_BODY)
    assert r.status_code == 422
    assert "manual review" in r.json()["detail"].lower()


def test_finalize_without_risk_evaluation_is_refused(app_client):
    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]

    r = app_client.post(f"/intake/batches/{batch_id}/finalize", json=FINALIZE_BODY)
    assert r.status_code == 409
    assert r.json()["detail"] == "Batch has not been risk evaluated"


def test_finalize_rejected_batch_is_refused(app_client):
    from datetime import datetime

    from shared.database import get_connection
    from shared.schemas import RiskDecision
    from services.risk_engine.storage import ensure_risk_schema, save_decision

    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]

    conn = get_connection()
    ensure_risk_schema(conn)
    save_decision(
        conn,
        RiskDecision(
            batch_id=batch_id,
            risk_score=0.95,
            decision="REJECT",
            triggered_rule="RULE_EXPIRED_BATCH",
            shap_contributors=[],
            reasons=["test fixture"],
            decided_at=datetime.utcnow(),
            model_version="test-fixture",
        ),
    )

    r = app_client.post(f"/intake/batches/{batch_id}/finalize", json=FINALIZE_BODY)
    assert r.status_code == 409
    assert "REJECT" in r.json()["detail"]


def _hold_batch(batch_id: str) -> None:
    from datetime import datetime

    from shared.database import get_connection
    from shared.schemas import RiskDecision
    from services.risk_engine.storage import ensure_risk_schema, save_decision

    conn = get_connection()
    ensure_risk_schema(conn)
    save_decision(
        conn,
        RiskDecision(
            batch_id=batch_id,
            risk_score=0.5,
            decision="HOLD",
            triggered_rule=None,
            shap_contributors=[],
            reasons=["test fixture"],
            decided_at=datetime.utcnow(),
            model_version="test-fixture",
        ),
    )


def test_finalize_hold_without_override_reason_is_refused(app_client):
    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]
    _hold_batch(batch_id)

    r = app_client.post(f"/intake/batches/{batch_id}/finalize", json=FINALIZE_BODY)
    assert r.status_code == 422
    assert "override_reason" in r.json()["detail"]


def test_finalize_hold_with_override_reason_succeeds(app_client):
    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]
    _hold_batch(batch_id)

    body = {**FINALIZE_BODY, "role": "pharmacist", "override_reason": "Physically inspected, evidence sufficient"}
    r = app_client.post(f"/intake/batches/{batch_id}/finalize", json=body)
    assert r.status_code == 200
    assert r.json()["already_finalized"] is False


def test_finalize_receipt_records_receiver_identity(app_client):
    """The ledger's finalize event actor must be the person who received the
    batch, not a service name -- and the receipts table must carry the full
    identity plus signed_at (the e-signature)."""
    from shared.database import get_connection
    from services.risk_engine.storage import get_receipt

    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]
    accept_batch(batch_id)

    app_client.post(f"/intake/batches/{batch_id}/finalize", json=FINALIZE_BODY)

    trace = app_client.get(f"/ledger/trace/{batch_id}")
    finalize_events = [e for e in trace.json()["events"] if e["action"] == "BATCH_FINALIZED"]
    assert len(finalize_events) == 1
    assert finalize_events[0]["actor"] == FINALIZE_BODY["received_by"]

    receipt = get_receipt(get_connection(), batch_id)
    assert receipt is not None
    assert receipt["facility_id"] == FINALIZE_BODY["facility_id"]
    assert receipt["received_by"] == FINALIZE_BODY["received_by"]
    assert receipt["role"] == FINALIZE_BODY["role"]
    assert receipt["decision"] == "ACCEPT"
    assert receipt["override_reason"] is None
    assert receipt["signed_at"]