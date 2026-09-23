"""
services/risk_engine/features.py

Builds the exact ~8-feature vector used by the XGBoost model, in the exact
order defined by config.FEATURE_ORDER. Training and inference both import
`extract_feature_vector` from this file — there must be no drift between
them.

Missing-value policy (documented, not silent):
    days_to_expiry               -> if expiry_date missing, impute a large
                                     "safe-looking" value is FORBIDDEN (would
                                     fabricate safety); instead impute the
                                     dataset median at train time, and at
                                     inference use the same stored median
                                     from model_metadata.json.
    temp_log_deviation_count     -> 0 if log is empty (this is fine for ML;
                                     the *safety-critical* missing-cold-chain
                                     case is already caught by rules.py
                                     before features.py ever runs).
    supplier_reject_rate_3mo/6mo -> 0.0 if supplier is None or field missing,
                                     PLUS see note below on why this is safe.
    ocr_qr_match_score           -> imputed median if missing (severe cases
                                     are already caught by rules.py).
    physical_inspection_flag_count -> 0 if notes absent/empty.
    days_since_manufacture       -> imputed median if manufacture_date missing.
    batch_size                   -> imputed median if not tracked yet.
    shelf_life_remaining_pct     -> days_left / total_shelf_life_days, imputed
                                     median if either date is missing, or if
                                     the dates make the denominator <= 0
                                     (manufacture_date on/after expiry_date is
                                     malformed data, not a 0%/negative value).

NOTE on supplier_reject_rate defaulting to 0.0: this only happens when a
supplier truly has no batch history (Supplier.total_batches_supplied == 0),
which is a real "no information" case, not "known good" — kept as 0.0 to
match the Supplier schema's own default rather than inventing a different
convention. This is called out explicitly in tests (see
tests/test_risk_features.py::test_missing_supplier_history).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from services.risk_engine import config
from services.risk_engine.config import COLD_CHAIN_SAFE_MIN_C, COLD_CHAIN_SAFE_MAX_C
from shared.schemas import Batch, Supplier


@dataclass(frozen=True)
class FeatureImputationDefaults:
    """Median values computed once at training time and reused at inference,
    so train/inference preprocessing never drifts. Loaded from
    model_metadata.json — see training/train_model.py.

    shelf_life_remaining_pct_median has a literal default (0.5) rather than
    being required: it lets any code (including existing tests) that
    constructs this dataclass without knowing about the newer feature keep
    working unchanged. A freshly trained model always supplies the real
    dataset median via model_metadata.json, overriding this default."""
    days_to_expiry_median: float
    ocr_qr_match_score_median: float
    days_since_manufacture_median: float
    batch_size_median: float
    shelf_life_remaining_pct_median: float = 0.5


def _days_between(start: date, end: date) -> int:
    return (end - start).days


def _count_temp_deviations(batch: Batch) -> int:
    count = 0
    for entry in batch.storage_temp_log:
        if entry.temp_c < COLD_CHAIN_SAFE_MIN_C or entry.temp_c > COLD_CHAIN_SAFE_MAX_C:
            count += 1
    return count


def _inspection_flag_count(batch: Batch) -> int:
    """
    ASSUMPTION (schema gap, flagging per master-doc rule): the shared Batch
    schema stores physical_inspection_notes as a free-text Optional[str],
    not a structured flag count. Until Person 2 / the team defines a
    structured field, this counts semicolon- or newline-separated non-empty
    entries as a rough proxy. Replace this with a real structured field if
    one gets added to shared/schemas.py — do not silently change the schema
    yourself.
    """
    notes = batch.physical_inspection_notes
    if not notes:
        return 0
    separators_normalized = notes.replace("\n", ";")
    parts = [p.strip() for p in separators_normalized.split(";")]
    return len([p for p in parts if p])


def _shelf_life_remaining_pct(batch: Batch, eval_date: date) -> Optional[float]:
    """days_left / total_shelf_life_days. None (imputed) when either date is
    missing, or when manufacture_date is on/after expiry_date -- that's
    malformed data, not a real 0%/negative value, so it must not be
    fabricated as one."""
    if batch.expiry_date is None or batch.manufacture_date is None:
        return None
    total_shelf_life_days = (batch.expiry_date - batch.manufacture_date).days
    if total_shelf_life_days <= 0:
        return None
    days_left = (batch.expiry_date - eval_date).days
    return days_left / total_shelf_life_days


def extract_raw_features(
    batch: Batch,
    supplier: Optional[Supplier],
    evaluation_date: Optional[date] = None,
) -> dict:
    """
    Returns a dict of raw (possibly None) feature values, keyed exactly by
    config.FEATURE_ORDER. None means "missing" and must be resolved by
    apply_imputation() before being handed to the model — this function
    itself NEVER fabricates a value.
    """
    eval_date = evaluation_date or date.today()

    days_to_expiry = (
        _days_between(eval_date, batch.expiry_date) if batch.expiry_date else None
    )
    days_since_manufacture = (
        _days_between(batch.manufacture_date, eval_date) if batch.manufacture_date else None
    )

    if supplier is not None:
        reject_3mo = supplier.reject_rate_3mo
        reject_6mo = supplier.reject_rate_6mo
    else:
        reject_3mo = 0.0
        reject_6mo = 0.0

    raw = {
        "days_to_expiry": days_to_expiry,
        "temp_log_deviation_count": float(_count_temp_deviations(batch)),
        "supplier_reject_rate_3mo": reject_3mo,
        "supplier_reject_rate_6mo": reject_6mo,
        "ocr_qr_match_score": batch.ocr_qr_match_score,
        "physical_inspection_flag_count": float(_inspection_flag_count(batch)),
        "days_since_manufacture": days_since_manufacture,
        "batch_size": None,  # batch_size is not yet in shared Batch schema —
                              # see note below.
        "shelf_life_remaining_pct": _shelf_life_remaining_pct(batch, eval_date),
    }
    return raw


# ASSUMPTION / SCHEMA GAP: shared Batch schema has no `batch_size` field.
# Flagging per master-doc rule rather than adding it silently. Until the
# team adds batch_size to shared/schemas.py, this feature is always
# imputed (median) — it still occupies its slot in FEATURE_ORDER so the
# model's feature count/order stays stable if the field is added later.


def apply_imputation(raw: dict, defaults: FeatureImputationDefaults) -> list:
    """
    Converts the raw dict into the final ordered feature vector, replacing
    None with the stored training-time median. Returns a list in
    config.FEATURE_ORDER order — order is load-bearing, do not change.
    """
    resolved = {
        "days_to_expiry": raw["days_to_expiry"] if raw["days_to_expiry"] is not None else defaults.days_to_expiry_median,
        "temp_log_deviation_count": raw["temp_log_deviation_count"],
        "supplier_reject_rate_3mo": raw["supplier_reject_rate_3mo"],
        "supplier_reject_rate_6mo": raw["supplier_reject_rate_6mo"],
        "ocr_qr_match_score": raw["ocr_qr_match_score"] if raw["ocr_qr_match_score"] is not None else defaults.ocr_qr_match_score_median,
        "physical_inspection_flag_count": raw["physical_inspection_flag_count"],
        "days_since_manufacture": raw["days_since_manufacture"] if raw["days_since_manufacture"] is not None else defaults.days_since_manufacture_median,
        "batch_size": raw["batch_size"] if raw["batch_size"] is not None else defaults.batch_size_median,
        "shelf_life_remaining_pct": raw["shelf_life_remaining_pct"] if raw["shelf_life_remaining_pct"] is not None else defaults.shelf_life_remaining_pct_median,
    }
    return [resolved[name] for name in config.FEATURE_ORDER]


def extract_feature_vector(
    batch: Batch,
    supplier: Optional[Supplier],
    defaults: FeatureImputationDefaults,
    evaluation_date: Optional[date] = None,
) -> list:
    """Convenience wrapper: raw extraction + imputation in one call. Used by
    both inference (model.py) and can be reused by training if desired."""
    raw = extract_raw_features(batch, supplier, evaluation_date)
    return apply_imputation(raw, defaults)