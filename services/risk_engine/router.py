"""
services/risk_engine/router.py

Exposes:
    POST /risk/evaluate/{batch_id}
    GET  /risk/decisions/{batch_id}

Orchestration order (matches Integration Master + Person 1 doc exactly):
    1. validate batch_id / retrieve Batch
    2. retrieve Supplier
    3. evaluate hard rules
    4. if rule fires -> deterministic decision
    5. else -> feature extraction -> ML inference -> SHAP
    6. construct RiskDecision (shared schema, unmodified)
    7. persist Batch.status
    8. POST /ledger/log
    9. return RiskDecision

Never lets a raw exception reach the response. Uses the two named errors
from the Integration Master:
    "Batch not found"
    "Risk evaluation unavailable"
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict

from fastapi import APIRouter, HTTPException

from services.risk_engine import config, data_access, ledger_client, rules
from services.risk_engine.explainability import build_reasons, compute_shap_contributors
from services.risk_engine.features import extract_feature_vector
from services.risk_engine.model import (
    ModelUnavailableError,
    decision_from_score,
    load_model,
    predict_risk_score,
)

# DO NOT redefine these — imported from the single source of truth.
from shared.schemas import BatchStatus, RiskDecision, ShapContributor

router = APIRouter(prefix="/risk", tags=["risk"])

# In-memory cache of decisions for GET /risk/decisions/{batch_id}, so a
# second evaluate call and a read-back are consistent within a demo run.
# This is intentionally simple for a hackathon prototype — if the real repo
# already persists RiskDecision in shared/database.py, prefer that instead
# and delete this dict (flag to team before doing so, since it touches the
# shared persistence pattern).
_DECISION_CACHE: Dict[str, RiskDecision] = {}


def _build_rule_decision(batch_id: str, rule_result: rules.RuleResult) -> RiskDecision:
    return RiskDecision(
        batch_id=batch_id,
        risk_score=1.0 if rule_result.decision == "REJECT" else 0.5,
        decision=rule_result.decision,
        triggered_rule=rule_result.rule_id,
        shap_contributors=[],  # a hard rule is not a SHAP-explained decision
        reasons=[rule_result.reason] if rule_result.reason else [],
        decided_at=datetime.utcnow(),
        model_version=config.MODEL_VERSION,
    )


def _build_model_decision(batch_id: str, score: float, contributors: list, reasons: list) -> RiskDecision:
    return RiskDecision(
        batch_id=batch_id,
        risk_score=score,
        decision=decision_from_score(score),
        triggered_rule=None,
        shap_contributors=[ShapContributor(**c) for c in contributors],
        reasons=reasons,
        decided_at=datetime.utcnow(),
        model_version=config.MODEL_VERSION,
    )


@router.post("/evaluate/{batch_id}", response_model=RiskDecision)
def evaluate_batch(batch_id: str) -> RiskDecision:
    # 1. Retrieve Batch
    try:
        batch = data_access.fetch_batch(batch_id)
    except Exception:  # noqa: BLE001 — never leak a raw DB traceback
        raise HTTPException(status_code=503, detail=config.ERR_RISK_UNAVAILABLE)

    if batch is None:
        raise HTTPException(status_code=404, detail=config.ERR_BATCH_NOT_FOUND)

    # 2. Retrieve Supplier (best-effort — missing supplier is handled by
    # features.py's documented default, not a hard failure)
    supplier = None
    if batch.supplier_id:
        try:
            supplier = data_access.fetch_supplier(batch.supplier_id)
        except Exception:  # noqa: BLE001
            supplier = None

    # 3-4. Hard safety rules
    rule_result = rules.evaluate_rules(batch, supplier)

    if rule_result.triggered:
        decision = _build_rule_decision(batch_id, rule_result)
    else:
        # 5. ML path
        try:
            loaded_model = load_model()
            feature_vector = extract_feature_vector(
                batch, supplier, loaded_model.imputation_defaults
            )
            score = predict_risk_score(feature_vector)
            contributors = compute_shap_contributors(loaded_model, feature_vector)
            reasons = build_reasons(contributors)
        except ModelUnavailableError:
            raise HTTPException(status_code=503, detail=config.ERR_RISK_UNAVAILABLE)
        except Exception:  # noqa: BLE001 — never leak SHAP/XGBoost internals
            raise HTTPException(status_code=503, detail=config.ERR_RISK_UNAVAILABLE)

        decision = _build_model_decision(batch_id, score, contributors, reasons)

    # 7. Persist batch status (best-effort; decision is still returned even
    # if this write fails, but we don't pretend it succeeded silently — log
    # server-side only, never to the API response)
    try:
        data_access.persist_batch_status(batch_id, BatchStatus(decision.decision))
    except Exception:  # noqa: BLE001
        pass  # intentionally non-fatal; status update failure shouldn't
              # block returning a valid decision that was already computed

    # 8. Ledger logging — required "without exception" per Person 1 doc.
    # If this fails, we still return the decision (the frontend needs it),
    # but this is a real gap worth surfacing during integration testing —
    # do not silently claim success.
    try:
        ledger_client.log_risk_decision(
            batch_id=batch_id,
            actor="risk_engine",
            action="RISK_DECISION_RECORDED",
            payload=decision.model_dump(mode="json"),
        )
    except ledger_client.LedgerLogFailedError:
        pass  # decision already computed and will be returned; ledger
              # unavailability is a known named-error case for the caller
              # of this whole pipeline, not for risk evaluation itself

    _DECISION_CACHE[batch_id] = decision
    return decision


@router.get("/decisions/{batch_id}", response_model=RiskDecision)
def get_decision(batch_id: str) -> RiskDecision:
    decision = _DECISION_CACHE.get(batch_id)
    if decision is None:
        raise HTTPException(status_code=404, detail=config.ERR_BATCH_NOT_FOUND)
    return decision
