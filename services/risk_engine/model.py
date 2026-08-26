"""
services/risk_engine/model.py

Loads and serves the trained XGBoost artifact. This module NEVER trains a
model — see training/train_model.py for that. It only loads the artifact
once (module-level cache) and serves predictions.

model_metadata.json (written by the training script) is expected to contain:
{
    "model_version": "risk-xgb-v1.0",
    "feature_order": [...],               # must match config.FEATURE_ORDER
    "trained_at": "2026-08-01T12:00:00",
    "imputation_defaults": {
        "days_to_expiry_median": ...,
        "ocr_qr_match_score_median": ...,
        "days_since_manufacture_median": ...,
        "batch_size_median": ...
    },
    "metrics": { "precision": ..., "recall": ..., "f1": ..., "roc_auc": ... },
    "notes": "Prototype model trained on synthetic data modeling realistic
              failure patterns; deployment would require validated hospital
              data, calibration and clinical validation."
}
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from services.risk_engine import config
from services.risk_engine.features import FeatureImputationDefaults


class ModelUnavailableError(Exception):
    """Raised when the model artifact cannot be loaded or inference fails.
    router.py catches this and returns the named error
    'Risk evaluation unavailable' — never a raw traceback."""


@dataclass
class LoadedModel:
    booster: object              # xgboost.Booster (typed as object to avoid
                                  # hard import at module-parse time if xgboost
                                  # isn't installed yet during doc review)
    model_version: str
    feature_order: tuple
    imputation_defaults: FeatureImputationDefaults


_CACHE: Optional[LoadedModel] = None


def _artifact_paths() -> tuple[str, str]:
    model_path = os.path.join(config.MODEL_ARTIFACT_DIR, config.MODEL_ARTIFACT_FILENAME)
    metadata_path = os.path.join(config.MODEL_ARTIFACT_DIR, config.MODEL_METADATA_FILENAME)
    return model_path, metadata_path


def load_model(force_reload: bool = False) -> LoadedModel:
    """
    Loads the trained model + metadata once and caches it at module level.
    Subsequent calls return the cached instance (per master-doc Section 22 —
    do not reinitialize expensive objects on every request).
    """
    global _CACHE
    if _CACHE is not None and not force_reload:
        return _CACHE

    model_path, metadata_path = _artifact_paths()

    if not os.path.exists(model_path) or not os.path.exists(metadata_path):
        raise ModelUnavailableError(
            f"Model artifact not found at '{model_path}' or metadata not "
            f"found at '{metadata_path}'. Run training/train_model.py first."
        )

    try:
        import xgboost as xgb
    except ImportError as exc:
        raise ModelUnavailableError(f"xgboost is not installed: {exc}") from exc

    try:
        booster = xgb.Booster()
        booster.load_model(model_path)

        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        feature_order = tuple(metadata["feature_order"])
        if feature_order != config.FEATURE_ORDER:
            raise ModelUnavailableError(
                "Model metadata feature_order does not match "
                "config.FEATURE_ORDER — refusing to serve predictions with "
                "a mismatched feature ordering. Retrain the model."
            )

        defaults = FeatureImputationDefaults(**metadata["imputation_defaults"])

        _CACHE = LoadedModel(
            booster=booster,
            model_version=metadata.get("model_version", config.MODEL_VERSION),
            feature_order=feature_order,
            imputation_defaults=defaults,
        )
        return _CACHE
    except ModelUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 — intentionally broad, converted
        raise ModelUnavailableError(f"Failed to load model artifact: {exc}") from exc


def predict_risk_score(feature_vector: list) -> float:
    """
    Returns a risk score in [0, 1]. This is the model's predicted probability
    of the positive ("risky") class — NOT a raw margin, NOT a clinically
    validated probability of harm. See config.py threshold comments.
    """
    try:
        import xgboost as xgb
    except ImportError as exc:
        raise ModelUnavailableError(f"xgboost is not installed: {exc}") from exc

    loaded = load_model()
    try:
        dmatrix = xgb.DMatrix([feature_vector], feature_names=list(loaded.feature_order))
        prediction = loaded.booster.predict(dmatrix)
        score = float(prediction[0])
    except Exception as exc:  # noqa: BLE001
        raise ModelUnavailableError(f"Inference failed: {exc}") from exc

    # Defensive clamp — the model is trained to output a probability, but
    # clamp defensively rather than let a slightly-out-of-range value leak
    # into a decision threshold comparison.
    return max(0.0, min(1.0, score))


def decision_from_score(score: float) -> str:
    if score < config.ACCEPT_THRESHOLD:
        return "ACCEPT"
    if score > config.REJECT_THRESHOLD:
        return "REJECT"
    return "HOLD"
