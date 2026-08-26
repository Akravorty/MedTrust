"""
services/risk_engine/tests/test_risk_rules.py

Note the file is prefixed test_risk_ (not just test_rules.py) to avoid the
cross-module test-file collisions the team hit before.
"""

from datetime import date, timedelta

import pytest

from services.risk_engine import rules
from shared.schemas import Batch, BatchStatus, Supplier, TempLogEntry


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


def test_expired_batch_rejected():
    batch = make_batch(expiry_date=date.today() - timedelta(days=1))
    result = rules.evaluate_rules(batch)
    assert result.triggered
    assert result.rule_id == rules.RULE_EXPIRED_BATCH
    assert result.decision == "REJECT"


def test_expiring_today_is_not_expired():
    # Expiry == evaluation date is not yet "before" it -> should not fire.
    batch = make_batch(expiry_date=date.today())
    result = rules.evaluate_rules(batch)
    assert not result.triggered


def test_missing_cold_chain_evidence_holds():
    batch = make_batch(medicine_name="Insulin Glargine", storage_temp_log=[])
    result = rules.evaluate_rules(batch)
    assert result.triggered
    assert result.rule_id == rules.RULE_MISSING_COLD_CHAIN
    assert result.decision == "HOLD"


def test_cold_chain_with_temp_log_does_not_trigger():
    batch = make_batch(
        medicine_name="Insulin Glargine",
        storage_temp_log=[TempLogEntry(timestamp=date.today(), temp_c=5.0)],
    )
    result = rules.evaluate_rules(batch)
    assert not result.triggered


def test_non_cold_chain_medicine_ignores_missing_temp_log():
    batch = make_batch(medicine_name="Paracetamol", storage_temp_log=[])
    result = rules.evaluate_rules(batch)
    assert not result.triggered


def test_severe_identity_mismatch():
    batch = make_batch(ocr_qr_match_score=0.1)
    result = rules.evaluate_rules(batch)
    assert result.triggered
    assert result.rule_id == rules.RULE_SEVERE_IDENTITY_MISMATCH


def test_mild_identity_mismatch_does_not_trigger_hard_rule():
    batch = make_batch(ocr_qr_match_score=0.8)
    result = rules.evaluate_rules(batch)
    assert not result.triggered


def test_missing_ocr_score_does_not_trigger_hard_rule():
    batch = make_batch(ocr_qr_match_score=None)
    result = rules.evaluate_rules(batch)
    assert not result.triggered


def test_clean_batch_triggers_no_rule():
    batch = make_batch()
    result = rules.evaluate_rules(batch)
    assert not result.triggered


def test_expiry_rule_checked_before_identity_rule():
    # Both conditions present -> expiry (checked first in the pipeline) wins.
    # This locks in the deterministic evaluation order documented in rules.py.
    batch = make_batch(
        expiry_date=date.today() - timedelta(days=1),
        ocr_qr_match_score=0.1,
    )
    result = rules.evaluate_rules(batch)
    assert result.rule_id == rules.RULE_EXPIRED_BATCH
