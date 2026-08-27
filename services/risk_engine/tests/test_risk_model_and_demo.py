"""
services/risk_engine/tests/test_risk_model_and_demo.py

Covers: threshold logic, demo-batch expected outcomes (via rules where
applicable), and the safety principle that ML never overrides a hard rule.

NOTE: tests that require the actual trained XGBoost artifact are marked with
`requires_model` and will be skipped automatically if
services/risk_engine/model/xgb_risk_model.json doesn't exist yet (e.g. before
training/train_model.py has been run against Person 6's dataset). This keeps
the rest of the suite runnable from day one without blocking on training.
"""

import os
from datetime import date, timedelta

import pytest

from services.risk_engine import config, rules
from services.risk_engine.model import decision_from_score
from shared.schemas import Batch, BatchStatus, TempLogEntry

MODEL_ARTIFACT_PATH = os.path.join(config.MODEL_ARTIFACT_DIR, config.MODEL_ARTIFACT_FILENAME)
requires_model = pytest.mark.skipif(
    not os.path.exists(MODEL_ARTIFACT_PATH),
    reason="Trained model artifact not present — run training/train_model.py first.",
)


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


# --------------------------------------------------------------------------
# Threshold logic
# --------------------------------------------------------------------------

def test_decision_from_score_accept():
    assert decision_from_score(0.1) == "ACCEPT"
    assert decision_from_score(0.29) == "ACCEPT"


def test_decision_from_score_hold():
    assert decision_from_score(0.3) == "HOLD"
    assert decision_from_score(0.5) == "HOLD"
    assert decision_from_score(0.7) == "HOLD"


def test_decision_from_score_reject():
    assert decision_from_score(0.71) == "REJECT"
    assert decision_from_score(1.0) == "REJECT"


# --------------------------------------------------------------------------
# Golden demo batches — DEMO-ACCEPT / DEMO-REJECT via rules
# (DEMO-HOLD and full pipeline correctness depend on the trained model +
# Person 6's actual seed data, so those are integration-level tests — see
# tests/test_full_pipeline.py at the repo root, owned jointly per the
# master doc.)
# --------------------------------------------------------------------------

def test_demo_accept_shape_triggers_no_hard_rule():
    batch = make_batch(
        batch_id="DEMO-ACCEPT",
        expiry_date=date.today() + timedelta(days=365),
        ocr_qr_match_score=1.0,
        storage_temp_log=[TempLogEntry(timestamp=date.today(), temp_c=5.0)],
    )
    result = rules.evaluate_rules(batch)
    assert not result.triggered


def test_demo_reject_shape_triggers_expiry_rule():
    batch = make_batch(
        batch_id="DEMO-REJECT",
        expiry_date=date.today() - timedelta(days=10),
    )
    result = rules.evaluate_rules(batch)
    assert result.triggered
    assert result.rule_id == rules.RULE_EXPIRED_BATCH
    assert result.decision == "REJECT"


# --------------------------------------------------------------------------
# Safety precedence: hard rule must never be overridden, even conceptually,
# by a low computed risk score. This test asserts the rule engine itself is
# rule-authoritative and independent of any model score.
# --------------------------------------------------------------------------

def test_hard_rule_is_independent_of_model_score():
    expired_batch = make_batch(expiry_date=date.today() - timedelta(days=1))
    result = rules.evaluate_rules(expired_batch)
    assert result.triggered
    assert result.decision == "REJECT"
    # No model score is consulted at all when a rule fires — this is
    # structural (router.py short-circuits before calling predict_risk_score)
    # rather than something a unit test on rules.py alone can measure
    # further; see tests/test_risk_router.py for the router-level assertion.


# --------------------------------------------------------------------------
# Model-dependent tests (skipped until a real artifact exists)
# --------------------------------------------------------------------------

@requires_model
def test_model_predicts_in_valid_range():
    from services.risk_engine.features import extract_feature_vector
    from services.risk_engine.model import load_model, predict_risk_score

    loaded = load_model()
    batch = make_batch()
    vector = extract_feature_vector(batch, None, loaded.imputation_defaults)
    score = predict_risk_score(vector)
    assert 0.0 <= score <= 1.0


@requires_model
def test_model_inference_is_deterministic():
    from services.risk_engine.features import extract_feature_vector
    from services.risk_engine.model import load_model, predict_risk_score

    loaded = load_model()
    batch = make_batch()
    vector = extract_feature_vector(batch, None, loaded.imputation_defaults)
    score_1 = predict_risk_score(vector)
    score_2 = predict_risk_score(vector)
    assert score_1 == score_2
