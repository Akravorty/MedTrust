"""
services/risk_engine/explainability.py

Wraps SHAP around the loaded XGBoost model to produce ShapContributor
objects matching the shared schema exactly. This module NEVER fabricates
explanation values — every number here is derived from an actual SHAP
computation against the actual loaded model and the actual feature vector
that produced the decision.
"""

from __future__ import annotations

from services.risk_engine import config
from services.risk_engine.model import LoadedModel, ModelUnavailableError

# Centralized internal-feature -> human-readable label mapping.
# Do not duplicate this mapping anywhere else in the codebase.
FEATURE_DISPLAY_LABELS = {
    "days_to_expiry": "Time to expiry",
    "temp_log_deviation_count": "Temperature excursion",
    "supplier_reject_rate_3mo": "Supplier trend (3-month)",
    "supplier_reject_rate_6mo": "Supplier trend (6-month)",
    "ocr_qr_match_score": "OCR / QR identity match",
    "physical_inspection_flag_count": "Physical inspection flags",
    "days_since_manufacture": "Batch age since manufacture",
    "batch_size": "Batch size",
    "shelf_life_remaining_pct": "Shelf life remaining (%)",
}

TOP_N_CONTRIBUTORS = 4


def _direction(shap_value: float) -> str:
    return "increases_risk" if shap_value > 0 else "decreases_risk"


def compute_shap_contributors(loaded: LoadedModel, feature_vector: list) -> list[dict]:
    """
    Returns a list of dicts shaped exactly like shared.schemas.ShapContributor
    (router.py constructs the actual pydantic objects — this stays a plain
    dict so this module has no hard dependency on the shared schema import
    path, easing standalone unit testing).
    """
    try:
        import shap
        import numpy as np
    except ImportError as exc:
        raise ModelUnavailableError(f"shap is not installed: {exc}") from exc

    try:
        explainer = shap.TreeExplainer(loaded.booster)
        shap_values = explainer.shap_values(np.array([feature_vector]))
        # shap_values shape: (1, n_features) for binary XGBoost booster output
        values = shap_values[0] if hasattr(shap_values, "__len__") else shap_values
    except Exception as exc:  # noqa: BLE001
        raise ModelUnavailableError(f"SHAP computation failed: {exc}") from exc

    contributors = []
    for feature_name, shap_value in zip(loaded.feature_order, values):
        contributors.append(
            {
                "feature": feature_name,
                "display_label": FEATURE_DISPLAY_LABELS.get(feature_name, feature_name),
                "contribution": float(shap_value),
                "direction": _direction(float(shap_value)),
            }
        )

    # Rank by absolute contribution, take top N — stable sort keeps
    # deterministic tie-breaking by original feature order.
    contributors.sort(key=lambda c: abs(c["contribution"]), reverse=True)
    return contributors[:TOP_N_CONTRIBUTORS]


def build_reasons(contributors: list[dict]) -> list[str]:
    """
    Converts top contributors into judge/staff-readable sentences. Every
    sentence is traceable to an actual model feature — no invented medical
    claims.
    """
    reason_templates = {
        "temp_log_deviation_count": "Temperature excursions increased the estimated risk.",
        "supplier_reject_rate_3mo": "This supplier's recent (3-month) rejection rate is elevated.",
        "supplier_reject_rate_6mo": "This supplier's longer-term (6-month) rejection rate is elevated.",
        "ocr_qr_match_score": "OCR and QR identity match is below the expected confidence level.",
        "physical_inspection_flag_count": "Physical inspection notes flagged one or more concerns.",
        "days_to_expiry": "This batch is close to its expiry date.",
        "days_since_manufacture": "This batch's age since manufacture is contributing to the risk estimate.",
        "batch_size": "Batch size is contributing to the risk estimate.",
        "shelf_life_remaining_pct": "This batch has a low proportion of its total shelf life remaining.",
    }

    reasons = []
    for c in contributors:
        if c["direction"] != "increases_risk":
            continue
        template = reason_templates.get(c["feature"])
        if template:
            reasons.append(template)

    if not reasons:
        reasons.append(
            "No single factor dominated; risk reflects a combination of "
            "weaker signals below individual reporting thresholds."
        )
    return reasons