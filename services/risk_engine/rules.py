"""
services/risk_engine/rules.py

Deterministic hard-safety rule engine.

CRITICAL PRINCIPLE: these rules ALWAYS win over the ML model. If any rule
triggers, the ML model is skipped entirely and RiskDecision.triggered_rule
is set to the rule's stable ID.

Rule IDs (stable, machine-readable — never change casually, other modules
may reference these strings, e.g. Person 4's Q&A agent explaining "why"):
    RULE_EXPIRED_BATCH
    RULE_MISSING_COLD_CHAIN
    RULE_SEVERE_IDENTITY_MISMATCH
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from services.risk_engine import config

# Import shared schemas. In the real repo these come from shared/schemas.py.
# DO NOT redefine Batch/Supplier here — this import is the only source.
from shared.schemas import Batch, Supplier

RULE_EXPIRED_BATCH = "RULE_EXPIRED_BATCH"
RULE_MISSING_COLD_CHAIN = "RULE_MISSING_COLD_CHAIN"
RULE_SEVERE_IDENTITY_MISMATCH = "RULE_SEVERE_IDENTITY_MISMATCH"


@dataclass(frozen=True)
class RuleResult:
    """Internal representation of a fired rule. Never returned directly to
    the API — router.py maps this onto the shared RiskDecision schema."""
    triggered: bool
    rule_id: Optional[str]
    decision: Optional[str]      # "ACCEPT" | "HOLD" | "REJECT"
    reason: Optional[str]
    severity: Optional[str]      # "critical" | "moderate" — for internal logging only


def _no_trigger() -> RuleResult:
    return RuleResult(triggered=False, rule_id=None, decision=None, reason=None, severity=None)


def _requires_cold_chain(batch: Batch) -> bool:
    """
    See config.py for the documented assumption: the shared Batch schema has
    no explicit requires_cold_chain field, so this infers it from
    medicine_name. Change this single function if/when the team adds a real
    field to shared/schemas.py.
    """
    name = (batch.medicine_name or "").lower()
    return any(keyword in name for keyword in config.COLD_CHAIN_MEDICINE_KEYWORDS)


def _rule_expired_batch(batch: Batch, evaluation_date: date) -> RuleResult:
    if batch.expiry_date is None:
        # No expiry date recorded at all is itself a data-quality problem,
        # but per the master doc's error taxonomy this is not a hard safety
        # rule — leave it to fall through (ML features handle missing dates
        # via an explicit missing-value strategy, see features.py).
        return _no_trigger()

    if batch.expiry_date < evaluation_date:
        return RuleResult(
            triggered=True,
            rule_id=RULE_EXPIRED_BATCH,
            decision="REJECT",
            reason=(
                f"Batch expired on {batch.expiry_date.isoformat()}, which is "
                f"before the evaluation date {evaluation_date.isoformat()}."
            ),
            severity="critical",
        )
    return _no_trigger()


def _rule_missing_cold_chain(batch: Batch) -> RuleResult:
    if not _requires_cold_chain(batch):
        return _no_trigger()

    if len(batch.storage_temp_log) < config.MIN_TEMP_LOG_ENTRIES_REQUIRED:
        return RuleResult(
            triggered=True,
            rule_id=RULE_MISSING_COLD_CHAIN,
            decision="HOLD",
            reason=(
                f"'{batch.medicine_name}' requires cold-chain handling, but no "
                f"temperature log evidence was recorded for this batch."
            ),
            severity="moderate",
        )
    return _no_trigger()


def _rule_severe_identity_mismatch(batch: Batch) -> RuleResult:
    score = batch.ocr_qr_match_score
    if score is None:
        # Missing score is a data-quality gap, not itself a severe mismatch —
        # handled as a missing ML feature, not a hard rule.
        return _no_trigger()

    if score < config.OCR_QR_SEVERE_MISMATCH_THRESHOLD:
        return RuleResult(
            triggered=True,
            rule_id=RULE_SEVERE_IDENTITY_MISMATCH,
            decision=config.SEVERE_MISMATCH_DECISION,
            reason=(
                f"OCR-extracted label text does not reliably match the QR "
                f"payload (match score {score:.2f}, below the "
                f"{config.OCR_QR_SEVERE_MISMATCH_THRESHOLD} threshold) — "
                f"batch identity cannot be confidently confirmed."
            ),
            severity="critical",
        )
    return _no_trigger()


# Deterministic evaluation order. Expiry is checked first because it is the
# most unambiguous safety condition; identity mismatch second because it
# undermines trust in every other field; cold-chain evidence last.
_RULE_PIPELINE = (
    _rule_expired_batch,
    _rule_severe_identity_mismatch,
    _rule_missing_cold_chain,
)


def evaluate_rules(
    batch: Batch,
    supplier: Optional[Supplier] = None,
    evaluation_date: Optional[date] = None,
) -> RuleResult:
    """
    Runs all hard safety rules in a fixed, deterministic order and returns
    the FIRST one that triggers. supplier is accepted for signature symmetry
    / future rules but is not currently used by any rule.
    """
    eval_date = evaluation_date or date.today()

    for rule_fn in _RULE_PIPELINE:
        if rule_fn is _rule_expired_batch:
            result = rule_fn(batch, eval_date)
        else:
            result = rule_fn(batch)
        if result.triggered:
            return result

    return _no_trigger()
