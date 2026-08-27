"""
services/risk_engine/tests/test_risk_features.py
"""

from datetime import date, timedelta

from services.risk_engine import config
from services.risk_engine.features import (
    FeatureImputationDefaults,
    apply_imputation,
    extract_feature_vector,
    extract_raw_features,
)
from shared.schemas import Batch, BatchStatus, Supplier, TempLogEntry


DEFAULTS = FeatureImputationDefaults(
    days_to_expiry_median=180.0,
    ocr_qr_match_score_median=0.9,
    days_since_manufacture_median=60.0,
    batch_size_median=500.0,
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


def make_supplier(**overrides) -> Supplier:
    defaults = dict(
        supplier_id="S1",
        name="Acme Pharma",
        reject_rate_3mo=0.05,
        reject_rate_6mo=0.04,
        total_batches_supplied=40,
        flagged_incidents=[],
    )
    defaults.update(overrides)
    return Supplier(**defaults)


def test_days_to_expiry_computed_correctly():
    batch = make_batch(expiry_date=date.today() + timedelta(days=100))
    raw = extract_raw_features(batch, None)
    assert raw["days_to_expiry"] == 100


def test_days_since_manufacture_computed_correctly():
    batch = make_batch(manufacture_date=date.today() - timedelta(days=45))
    raw = extract_raw_features(batch, None)
    assert raw["days_since_manufacture"] == 45


def test_temperature_deviation_count():
    batch = make_batch(
        storage_temp_log=[
            TempLogEntry(timestamp=date.today(), temp_c=5.0),   # in-range
            TempLogEntry(timestamp=date.today(), temp_c=12.0),  # out of range
            TempLogEntry(timestamp=date.today(), temp_c=-1.0),  # out of range
        ]
    )
    raw = extract_raw_features(batch, None)
    assert raw["temp_log_deviation_count"] == 2.0


def test_supplier_rates_pulled_from_supplier():
    batch = make_batch()
    supplier = make_supplier(reject_rate_3mo=0.12, reject_rate_6mo=0.09)
    raw = extract_raw_features(batch, supplier)
    assert raw["supplier_reject_rate_3mo"] == 0.12
    assert raw["supplier_reject_rate_6mo"] == 0.09


def test_missing_supplier_history_defaults_to_zero_not_fabricated():
    # No supplier at all -> 0.0, documented in features.py module docstring.
    batch = make_batch()
    raw = extract_raw_features(batch, None)
    assert raw["supplier_reject_rate_3mo"] == 0.0
    assert raw["supplier_reject_rate_6mo"] == 0.0


def test_inspection_flag_count_from_notes():
    batch = make_batch(physical_inspection_notes="torn label; damp packaging")
    raw = extract_raw_features(batch, None)
    assert raw["physical_inspection_flag_count"] == 2.0


def test_inspection_flag_count_empty_when_no_notes():
    batch = make_batch(physical_inspection_notes=None)
    raw = extract_raw_features(batch, None)
    assert raw["physical_inspection_flag_count"] == 0.0


def test_missing_expiry_date_is_imputed_not_fabricated_as_safe():
    batch = make_batch(expiry_date=None)
    raw = extract_raw_features(batch, None)
    assert raw["days_to_expiry"] is None  # raw stays None
    vector = apply_imputation(raw, DEFAULTS)
    idx = config.FEATURE_ORDER.index("days_to_expiry")
    assert vector[idx] == DEFAULTS.days_to_expiry_median


def test_feature_vector_order_matches_config():
    batch = make_batch()
    supplier = make_supplier()
    vector = extract_feature_vector(batch, supplier, DEFAULTS)
    assert len(vector) == len(config.FEATURE_ORDER)
    # spot check: temp_log_deviation_count position holds a float, not None
    idx = config.FEATURE_ORDER.index("temp_log_deviation_count")
    assert isinstance(vector[idx], float)


def test_batch_size_always_imputed_pending_schema_field():
    # Documented schema gap: batch_size doesn't exist on Batch yet.
    batch = make_batch()
    vector = extract_feature_vector(batch, None, DEFAULTS)
    idx = config.FEATURE_ORDER.index("batch_size")
    assert vector[idx] == DEFAULTS.batch_size_median
