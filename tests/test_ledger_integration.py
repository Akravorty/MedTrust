"""
Verifies Intake calls the real ledger contract (never a parallel/direct
table write) on both creation and finalization, with the correct
batch_id and action.
"""

from .conftest import FINALIZE_BODY, accept_batch, make_label_image_with_qr


def test_creation_event_sent_to_ledger(app_client):
    raw = make_label_image_with_qr()
    r = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = r.json()["batch_id"]

    trace = app_client.get(f"/ledger/trace/{batch_id}")
    assert trace.status_code == 200
    actions = [e["action"] for e in trace.json()["events"]]
    assert "BATCH_CREATED" in actions


def test_finalization_event_sent_to_ledger(app_client):
    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]
    accept_batch(batch_id)
    app_client.post(f"/intake/batches/{batch_id}/finalize", json=FINALIZE_BODY)

    trace = app_client.get(f"/ledger/trace/{batch_id}")
    actions = [e["action"] for e in trace.json()["events"]]
    assert "BATCH_CREATED" in actions
    assert "BATCH_FINALIZED" in actions


def test_idempotent_finalize_does_not_duplicate_ledger_event(app_client):
    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]
    accept_batch(batch_id)

    app_client.post(f"/intake/batches/{batch_id}/finalize", json=FINALIZE_BODY)
    app_client.post(f"/intake/batches/{batch_id}/finalize", json=FINALIZE_BODY)  # second call, should be a no-op

    trace = app_client.get(f"/ledger/trace/{batch_id}")
    actions = [e["action"] for e in trace.json()["events"]]
    assert actions.count("BATCH_FINALIZED") == 1


def test_chain_verifies_after_intake_events(app_client):
    raw = make_label_image_with_qr()
    scan = app_client.post("/intake/scan", files={"file": ("a.png", raw, "image/png")})
    batch_id = scan.json()["batch_id"]
    accept_batch(batch_id)
    app_client.post(f"/intake/batches/{batch_id}/finalize", json=FINALIZE_BODY)

    trace = app_client.get(f"/ledger/trace/{batch_id}")
    assert trace.json()["chain_valid"] is True