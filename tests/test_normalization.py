from datetime import date

from services.intake.normalization import normalize_batch_number, normalize_medicine_name, normalize_date


def test_normalize_batch_number_variants():
    assert normalize_batch_number("BATCH-001") == "001"
    assert normalize_batch_number("batch 001") == "001"
    assert normalize_batch_number("BATCH : 001") == "001"
    assert normalize_batch_number("BATCH#001") == "001"
    assert normalize_batch_number("B12345") == "B12345"  # no "batch" label to strip


def test_normalize_batch_number_none_and_empty():
    assert normalize_batch_number(None) is None
    assert normalize_batch_number("   ") is None


def test_normalize_medicine_name_casing_and_whitespace():
    assert normalize_medicine_name("  paracetamol   500mg  ") == "Paracetamol 500Mg"
    assert normalize_medicine_name("IBUPROFEN") == "Ibuprofen"


def test_normalize_medicine_name_none():
    assert normalize_medicine_name(None) is None
    assert normalize_medicine_name("") is None


def test_normalize_date_formats():
    assert normalize_date("2027-12-31") == date(2027, 12, 31)
    assert normalize_date("31/12/2027") == date(2027, 12, 31)
    assert normalize_date("31-12-2027") == date(2027, 12, 31)
    assert normalize_date("12/2027") == date(2027, 12, 31)  # MM/YYYY -> last day of month


def test_normalize_date_ambiguous_returns_none():
    assert normalize_date("not a date") is None
    assert normalize_date("2027/13/45") is None
    assert normalize_date("32-13-2027") is None


def test_normalize_date_none_and_empty():
    assert normalize_date(None) is None
    assert normalize_date("") is None