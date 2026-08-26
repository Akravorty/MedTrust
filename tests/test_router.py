"""
API-level tests using FastAPI's TestClient. Uses an isolated in-memory-style
DB by monkeypatching shared.database's connection so tests don't touch the
real meditrust.db file.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

import shared.database as database_module
from services.ledger.chain import ensure_schema
import main


@pytest.fixture
def client(monkeypatch):
    test_conn = sqlite3.connect(":memory:", check_same_thread=False)
    test_conn.row_factory = sqlite3.Row
    ensure_schema(test_conn)

    monkeypatch.setattr(database_module, "_connection", test_conn)
    monkeypatch.setattr(database_module, "get_connection", lambda: test_conn)

    return TestClient(main.app)


def test_post_log_first_event(client):
    resp = client.post(
        "/ledger/log",
        json={"batch_id": "B1", "actor": "person2", "action": "BATCH_CREATED", "payload": {"n": 1}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["batch_id"] == "B1"
    assert body["prev_hash"] == "0" * 64


def test_post_log_subsequent_event_links_to_previous(client):
    r1 = client.post(
        "/ledger/log",
        json={"batch_id": "B1", "actor": "p2", "action": "BATCH_CREATED", "payload": {}},
    ).json()
    r2 = client.post(
        "/ledger/log",
        json={"batch_id": "B1", "actor": "p1", "action": "RISK_EVALUATED", "payload": {}},
    ).json()
    assert r2["prev_hash"] == r1["this_hash"]


def test_post_log_malformed_request(client):
    resp = client.post("/ledger/log", json={"actor": "p2"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Invalid request"


def test_post_log_duplicate_retry_with_idempotency_key(client):
    headers = {"Idempotency-Key": "retry-123"}
    body = {"batch_id": "B1", "actor": "p2", "action": "BATCH_CREATED", "payload": {}}
    r1 = client.post("/ledger/log", json=body, headers=headers).json()
    r2 = client.post("/ledger/log", json=body, headers=headers).json()
    assert r1["event_id"] == r2["event_id"]  # same logical op, not duplicated


def test_post_log_without_idempotency_key_allows_legitimate_repeats(client):
    body = {"batch_id": "B1", "actor": "p2", "action": "STATUS_CHANGED", "payload": {"note": "same"}}
    r1 = client.post("/ledger/log", json=body).json()
    r2 = client.post("/ledger/log", json=body).json()
    assert r1["event_id"] != r2["event_id"]  # no key given -> both are real events


def test_get_trace_correct_ordering(client):
    client.post("/ledger/log", json={"batch_id": "B1", "actor": "p2", "action": "BATCH_CREATED", "payload": {}})
    client.post("/ledger/log", json={"batch_id": "B1", "actor": "p1", "action": "RISK_EVALUATED", "payload": {}})

    resp = client.get("/ledger/trace/B1")
    assert resp.status_code == 200
    body = resp.json()
    actions = [e["action"] for e in body["events"]]
    assert actions == ["BATCH_CREATED", "RISK_EVALUATED"]
    assert body["chain_valid"] is True


def test_get_trace_nonexistent_batch(client):
    resp = client.get("/ledger/trace/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Batch not found"


def test_recall_simulate_missing_distribution_history(client):
    client.post("/ledger/log", json={"batch_id": "B1", "actor": "p2", "action": "BATCH_CREATED", "payload": {}})
    resp = client.post("/ledger/recall/simulate/B1", json={"triggered_by": "qa"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Recall trace incomplete"


def test_recall_simulate_demo_recall_deterministic(client):
    path = ["Central Store", "Pharmacy Store B", "Ward 3", "Ward 7"]
    client.post("/ledger/log", json={"batch_id": "DEMO-RECALL", "actor": "system", "action": "BATCH_CREATED", "payload": {}})
    for i, loc in enumerate(path):
        client.post(
            "/ledger/log",
            json={
                "batch_id": "DEMO-RECALL",
                "actor": "system",
                "action": "DISTRIBUTION_EVENT",
                "payload": {"location": loc, "sequence": i},
            },
        )
    resp = client.post("/ledger/recall/simulate/DEMO-RECALL", json={"triggered_by": "qa"})
    assert resp.status_code == 200
    assert resp.json()["affected_departments"] == path