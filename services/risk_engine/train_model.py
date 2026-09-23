from __future__ import annotations

import json
import os
from datetime import datetime

import numpy as np
import xgboost as xgb

from services.risk_engine import config


def _generate_synthetic_training_data(n_samples: int = 3000, seed: int = config.RANDOM_SEED):
    rng = np.random.default_rng(seed)
    n = n_samples
    days_to_expiry = rng.integers(-10, 730, size=n).astype(float)
    temp_dev = rng.poisson(0.4, size=n).astype(float)
    reject_3mo = np.clip(rng.normal(0.05, 0.05, size=n), 0, 1)
    reject_6mo = np.clip(rng.normal(0.05, 0.04, size=n), 0, 1)
    ocr_qr_match = np.clip(rng.normal(0.92, 0.12, size=n), 0, 1)
    inspection_flags = rng.poisson(0.2, size=n).astype(float)
    days_since_mfg = rng.integers(1, 900, size=n).astype(float)
    batch_size = rng.integers(50, 5000, size=n).astype(float)

    # Total shelf life (manufacture -> expiry) independent of days_to_expiry,
    # which is measured from *today*, not from manufacture. A short total
    # shelf life with little of it remaining is a stronger risk signal than
    # the same days_to_expiry on a long-shelf-life product, so this is a
    # genuinely different feature from days_to_expiry, not a rescaling of it.
    total_shelf_life_days = rng.integers(180, 1095, size=n).astype(float)
    shelf_life_remaining_pct = days_to_expiry / total_shelf_life_days

    risk_signal = (
        -0.004 * days_to_expiry + 0.35 * temp_dev + 3.0 * reject_3mo
        + 2.0 * reject_6mo + 2.5 * (1.0 - ocr_qr_match) + 0.25 * inspection_flags
        - 0.6 * shelf_life_remaining_pct
        + rng.normal(0, 0.4, size=n)
    )
    threshold = np.quantile(risk_signal, 0.75)
    label = (risk_signal > threshold).astype(int)

    X = np.column_stack([days_to_expiry, temp_dev, reject_3mo, reject_6mo,
                          ocr_qr_match, inspection_flags, days_since_mfg, batch_size,
                          shelf_life_remaining_pct])
    return X, label, {
        "days_to_expiry": days_to_expiry, "ocr_qr_match_score": ocr_qr_match,
        "days_since_manufacture": days_since_mfg, "batch_size": batch_size,
        "shelf_life_remaining_pct": shelf_life_remaining_pct,
    }


def main() -> None:
    X, y, medians_source = _generate_synthetic_training_data()
    n = len(y)
    idx = np.random.default_rng(config.RANDOM_SEED).permutation(n)
    split = int(n * 0.8)
    train_idx, test_idx = idx[:split], idx[split:]

    dtrain = xgb.DMatrix(X[train_idx], label=y[train_idx], feature_names=list(config.FEATURE_ORDER))
    dtest = xgb.DMatrix(X[test_idx], label=y[test_idx], feature_names=list(config.FEATURE_ORDER))
    params = {"objective": "binary:logistic", "eval_metric": "logloss", "max_depth": 4, "eta": 0.1, "seed": config.RANDOM_SEED}
    booster = xgb.train(params, dtrain, num_boost_round=150)

    from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score
    preds = booster.predict(dtest)
    pred_labels = (preds > 0.5).astype(int)
    metrics = {
        "precision": float(precision_score(y[test_idx], pred_labels, zero_division=0)),
        "recall": float(recall_score(y[test_idx], pred_labels, zero_division=0)),
        "f1": float(f1_score(y[test_idx], pred_labels, zero_division=0)),
        "roc_auc": float(roc_auc_score(y[test_idx], preds)),
    }

    os.makedirs(config.MODEL_ARTIFACT_DIR, exist_ok=True)
    model_path = os.path.join(config.MODEL_ARTIFACT_DIR, config.MODEL_ARTIFACT_FILENAME)
    metadata_path = os.path.join(config.MODEL_ARTIFACT_DIR, config.MODEL_METADATA_FILENAME)
    booster.save_model(model_path)

    metadata = {
        "model_version": config.MODEL_VERSION,
        "feature_order": list(config.FEATURE_ORDER),
        "trained_at": datetime.now().isoformat(),
        "imputation_defaults": {
            "days_to_expiry_median": float(np.median(medians_source["days_to_expiry"])),
            "ocr_qr_match_score_median": float(np.median(medians_source["ocr_qr_match_score"])),
            "days_since_manufacture_median": float(np.median(medians_source["days_since_manufacture"])),
            "batch_size_median": float(np.median(medians_source["batch_size"])),
            "shelf_life_remaining_pct_median": float(np.median(medians_source["shelf_life_remaining_pct"])),
        },
        "metrics": metrics,
        "notes": "Prototype model trained on synthetic data modeling realistic failure patterns; deployment would require validated hospital data, calibration and clinical validation.",
    }
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"Saved model to:    {model_path}")
    print(f"Saved metadata to: {metadata_path}")
    print(f"Metrics: {metrics}")


if __name__ == "__main__":
    main()