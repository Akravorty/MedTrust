"""
services/risk_engine/router.py

Exposes:
    POST /risk/evaluate/{batch_id}
    GET  /risk/decisions/{batch_id}

Orchestration order (matches Integration Master + Person 1 doc exactly):
    0. idempotency short-circuit (if Idempotency-Key header present)
    1. validate batch_id / retrieve Batch
    2. retrieve Supplier
    3. evaluate hard rules
    4. if rule fires -> deterministic decision
    5. else -> feature extraction -> ML inference -> SHAP
    6. construct RiskDecision (shared schema, unmodified)
    7. persist RiskDecision + Batch.status
    7b. record idempotency key -> decision (if header present)
    7c. if REJECT or HOLD -> raise an alert
    8. POST /ledger/log
    9. return RiskDecision

Never lets a raw exception reach the response. Uses the two named errors
from the Integration Master:
    "Batch not found"
    "Risk evaluation unavailable"
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException

from services.risk_engine import config, data_access, ledger_client, rules
from services.risk_engine import storage as risk_storage
from services.alerts import service as alerts_service
from services.risk_engine.explainability import build_reasons, compute_shap_contributors
from services.risk_engine.features import extract_feature_vector
from services.risk_engine.model import (
    ModelUnavailableError,
    decision_from_score,
    load_model,
    predict_risk_score,
)
from shared.database import get_connection

# DO NOT redefine these — imported from the single source of truth.
from shared.schemas import DECISION_TO_STATUS, RiskDecision, ShapContributor

logger = logging.getLogger("risk_engine.router")

router = APIRouter(prefix="/risk", tags=["risk"])

# Decisions serious enough to interrupt a human, not just update a screen.
ALERTABLE_DECISIONS = {"REJECT", "HOLD"}

# Placeholder recipient until a real per-facility QA-officer resolution
# step exists (the recall path has one via Step 7; risk decisions don't yet).
_DEFAULT_ALERT_RECIPIENT = "QA Officer"


def _ensure_idempotency_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_decision_idempotency (
            idempotency_key TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            decision_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _get_cached_decision(conn: sqlite3.Connection, idempotency_key: str) -> RiskDecision | None:
    """
    Looks up a previously-recorded response for this exact key, independent
    of whatever risk_decisions currently holds for the batch (that table is
    an upsert-by-batch_id "current state" table, not a history — see
    storage.py's own docstring on save_decision). The idempotency table
    stores its own snapshot of the decision so a replay returns exactly
    what the original request returned, even if the batch has since been
    re-evaluated for an unrelated reason.
    """
    _ensure_idempotency_schema(conn)
    row = conn.execute(
        "SELECT decision_json FROM risk_decision_idempotency WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if row is None:
        return None
    return RiskDecision.model_validate(json.loads(row["decision_json"]))


def _record_idempotency_key(
    conn: sqlite3.Connection, idempotency_key: str, batch_id: str, decision: RiskDecision
) -> None:
    _ensure_idempotency_schema(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO risk_decision_idempotency
            (idempotency_key, batch_id, decision_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            idempotency_key,
            batch_id,
            json.dumps(decision.model_dump(mode="json")),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()


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
def evaluate_batch(
    batch_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RiskDecision:
    conn = get_connection()

    # 0. Idempotency short-circuit. A replay carrying a key we've already
    # processed returns the original response verbatim instead of
    # re-deciding (which would also re-fire an alert and re-log to the
    # ledger — see 7b/7c/8 below).
    if idempotency_key:
        try:
            cached = _get_cached_decision(conn, idempotency_key)
            if cached is not None:
                return cached
        except sqlite3.Error:
            logger.error(
                "idempotency_lookup_failed key=%s", idempotency_key, exc_info=True
            )
            # fall through and evaluate normally rather than blocking the request

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

    # 6-7. Persist the decision itself, then the derived batch status.
    #
    # The decision is the audit-relevant artifact and is persisted first: if
    # the status write fails we still have a record of what was decided.
    # Both handlers catch sqlite3.Error only — a bare `except Exception` here
    # is what hid the ACCEPT/ACCEPTED vocabulary bug for weeks.
    try:
        risk_storage.save_decision(conn, decision)
    except sqlite3.Error:
        logger.error("risk_decision_persist_failed batch_id=%s", batch_id, exc_info=True)

    try:
        data_access.persist_batch_status(batch_id, DECISION_TO_STATUS[decision.decision])
    except sqlite3.Error:
        logger.error("batch_status_persist_failed batch_id=%s", batch_id, exc_info=True)
    except KeyError:
        # An unmapped decision string is a programming error, not a runtime
        # condition. Loud in the log, non-fatal for the caller.
        logger.error(
            "unmapped_decision batch_id=%s decision=%r", batch_id, decision.decision
        )

    # 7b. Record this key -> decision mapping so a replay of the same
    # request (same Idempotency-Key) short-circuits at step 0 above instead
    # of creating a second decision, a second alert, and a second ledger
    # entry for what is really one logical evaluation.
    if idempotency_key:
        try:
            _record_idempotency_key(conn, idempotency_key, batch_id, decision)
        except sqlite3.Error:
            logger.error(
                "idempotency_record_failed key=%s batch_id=%s",
                idempotency_key, batch_id, exc_info=True,
            )

    # 7c. Alerting — best effort, same philosophy as ledger logging below.
    # A REJECT/HOLD that nobody is notified about is exactly the gap this
    # module exists to close, but a failed alert must never fail the
    # decision response itself.
    if decision.decision in ALERTABLE_DECISIONS:
        try:
            alerts_service.send_alert(
                conn,
                batch_id=batch_id,
                recipient=_DEFAULT_ALERT_RECIPIENT,
                alert_type=decision.decision,
                channel=alerts_service.CHANNEL_LOG,
                language="HI",
            )
        except Exception:  # noqa: BLE001 — alerting must not block a real decision
            logger.error("alert_send_failed batch_id=%s", batch_id, exc_info=True)

    # 8. Ledger logging — required "without exception" per Person 1 doc.
    try:
        ledger_client.log_risk_decision(
            batch_id=batch_id,
            actor="risk_engine",
            action="RISK_DECISION_RECORDED",
            payload=decision.model_dump(mode="json"),
        )
    except ledger_client.LedgerLogFailedError:
        logger.warning("ledger_log_failed batch_id=%s", batch_id)

    return decision


@router.get("/decisions/{batch_id}", response_model=RiskDecision)
def get_decision(batch_id: str) -> RiskDecision:
    conn = get_connection()
    try:
        decision = risk_storage.get_decision(conn, batch_id)
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail=config.ERR_RISK_UNAVAILABLE)

    if decision is None:
        raise HTTPException(status_code=404, detail=config.ERR_BATCH_NOT_FOUND)
    return decision