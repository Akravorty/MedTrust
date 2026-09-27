"""
services/risk_engine/tests/test_risk_router.py

API-level tests. Uses monkeypatching to isolate the router from the real
database/ledger, per the master doc's "mock-first" philosophy — these tests
should pass even before Person 2/3's real endpoints exist.
"""

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.risk_engine import config, data_access, ledger_client, router as router_module
from shared.schemas import Batch, BatchStatus, Supplier


def make_batch(**overrides) -> Batch:
    defaults = dict(
        batch_id="B1",
        medicine_name="Paracetamol",
        batch_number="BN-1",
        supplier_id="S1",
        received_timestamp=date.today(),
        manufacture_date=date.today() - timedelta(days=30),
        expiry_date=date.today() + timedelta(days=300),
        ocr_qr_match_score=0.95,
        storage_temp_log=[],
        physical_inspection_notes=None,
        status=BatchStatus.PENDING,
    )
    defaults.update(overrides)
    return Batch(**defaults)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(router_module.router)

    # evaluate/get_decision persist to the risk_decisions and alerts tables, so give
    # every test its own throwaway database with the schema in place. Never the dev DB.
    import shared.database as database_module

    monkeypatch.setattr(database_module, "DB_PATH", tmp_path / "risk_router_test.db")
    monkeypatch.setattr(database_module, "_connection", None)
    database_module.init_db()

    # Never hit a real ledger in this test file.
    monkeypatch.setattr(data_access, "persist_batch_status", lambda *a, **k: None)
    monkeypatch.setattr(
        ledger_client, "log_risk_decision", lambda *a, **k: {"event_id": "evt-1"}
    )

    return TestClient(app)


def test_unknown_batch_returns_named_error(client, monkeypatch):
    monkeypatch.setattr(data_access, "fetch_batch", lambda batch_id: None)

    response = client.post("/risk/evaluate/NOPE")
    assert response.status_code == 404
    assert response.json()["detail"] == config.ERR_BATCH_NOT_FOUND


def test_expired_batch_short_circuits_to_reject_without_model(client, monkeypatch):
    expired = make_batch(expiry_date=date.today() - timedelta(days=5))
    monkeypatch.setattr(data_access, "fetch_batch", lambda batch_id: expired)
    monkeypatch.setattr(data_access, "fetch_supplier", lambda supplier_id: None)

    # If the router tried to call the model for a rule-triggered batch, this
    # would raise — proving the ML path is never touched when a rule fires.
    def _boom(*args, **kwargs):
        raise AssertionError("Model should not be called when a hard rule fires")

    monkeypatch.setattr(router_module, "load_model", _boom)
    monkeypatch.setattr(router_module, "predict_risk_score", _boom)

    response = client.post("/risk/evaluate/B1")
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "REJECT"
    assert body["triggered_rule"] == "RULE_EXPIRED_BATCH"
    assert body["shap_contributors"] == []


def test_model_unavailable_returns_named_error(client, monkeypatch):
    clean_batch = make_batch()
    monkeypatch.setattr(data_access, "fetch_batch", lambda batch_id: clean_batch)
    monkeypatch.setattr(data_access, "fetch_supplier", lambda supplier_id: None)

    from services.risk_engine.model import ModelUnavailableError

    def _raise(*args, **kwargs):
        raise ModelUnavailableError("no artifact")

    monkeypatch.setattr(router_module, "load_model", _raise)

    response = client.post("/risk/evaluate/B1")
    assert response.status_code == 503
    assert response.json()["detail"] == config.ERR_RISK_UNAVAILABLE


def test_ledger_failure_does_not_block_returning_a_computed_rule_decision(client, monkeypatch):
    expired = make_batch(expiry_date=date.today() - timedelta(days=1))
    monkeypatch.setattr(data_access, "fetch_batch", lambda batch_id: expired)
    monkeypatch.setattr(data_access, "fetch_supplier", lambda supplier_id: None)

    def _raise(*args, **kwargs):
        raise ledger_client.LedgerLogFailedError("ledger down")

    monkeypatch.setattr(router_module.ledger_client, "log_risk_decision", _raise)

    response = client.post("/risk/evaluate/B1")
    assert response.status_code == 200
    assert response.json()["decision"] == "REJECT"


def test_repeated_evaluation_is_idempotent_for_a_rule_triggered_batch(client, monkeypatch):
    expired = make_batch(expiry_date=date.today() - timedelta(days=1))
    monkeypatch.setattr(data_access, "fetch_batch", lambda batch_id: expired)
    monkeypatch.setattr(data_access, "fetch_supplier", lambda supplier_id: None)

    first = client.post("/risk/evaluate/B1").json()
    second = client.post("/risk/evaluate/B1").json()
    assert first["decision"] == second["decision"] == "REJECT"
    assert first["triggered_rule"] == second["triggered_rule"] == "RULE_EXPIRED_BATCH"


def test_get_decision_after_evaluate(client, monkeypatch):
    expired = make_batch(expiry_date=date.today() - timedelta(days=1))
    monkeypatch.setattr(data_access, "fetch_batch", lambda batch_id: expired)
    monkeypatch.setattr(data_access, "fetch_supplier", lambda supplier_id: None)

    client.post("/risk/evaluate/B1")
    response = client.get("/risk/decisions/B1")
    assert response.status_code == 200
    assert response.json()["decision"] == "REJECT"


def test_get_decision_unknown_batch_returns_named_error(client):
    response = client.get("/risk/decisions/NEVER-EVALUATED")
    assert response.status_code == 404
    assert response.json()["detail"] == config.ERR_BATCH_NOT_FOUND
