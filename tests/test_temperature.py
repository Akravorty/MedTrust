from services.intake.temperature import validate_temperature_csv


def test_valid_readings():
    csv_text = "timestamp,temp_c\n2026-01-01T10:00:00,4.5\n2026-01-01T11:00:00,4.7\n"
    result = validate_temperature_csv(csv_text)
    assert result.status == "present"
    assert len(result.valid_entries) == 2
    assert result.invalid_rows == []


def test_unsorted_readings_get_sorted():
    csv_text = "timestamp,temp_c\n2026-01-01T12:00:00,5.0\n2026-01-01T10:00:00,4.0\n2026-01-01T11:00:00,4.5\n"
    result = validate_temperature_csv(csv_text)
    timestamps = [e.timestamp for e in result.valid_entries]
    assert timestamps == sorted(timestamps)


def test_malformed_row_preserved_as_invalid_not_discarded_silently():
    csv_text = "timestamp,temp_c\n2026-01-01T10:00:00,4.5\nnot-a-timestamp,4.7\n2026-01-01T11:00:00,not-a-number\n"
    result = validate_temperature_csv(csv_text)
    assert len(result.valid_entries) == 1
    assert len(result.invalid_rows) == 2
    reasons = {r["reason"] for r in result.invalid_rows}
    assert "invalid_timestamp" in reasons
    assert "non_numeric_temperature" in reasons


def test_missing_temperature_evidence():
    result = validate_temperature_csv(None)
    assert result.status == "missing"
    assert result.valid_entries == []

    result2 = validate_temperature_csv("   ")
    assert result2.status == "missing"


def test_invalid_timestamp_row():
    csv_text = "timestamp,temp_c\nnot-a-date,4.5\n"
    result = validate_temperature_csv(csv_text)
    assert result.status == "malformed"
    assert result.invalid_rows[0]["reason"] == "invalid_timestamp"


def test_duplicate_timestamp_counted_not_silently_dropped():
    csv_text = "timestamp,temp_c\n2026-01-01T10:00:00,4.5\n2026-01-01T10:00:00,4.9\n"
    result = validate_temperature_csv(csv_text)
    assert len(result.valid_entries) == 1  # first occurrence kept
    assert result.duplicate_count == 1
    assert result.status == "partial"


def test_empty_csv():
    result = validate_temperature_csv("")
    assert result.status == "missing"


def test_partially_corrupted_csv_preserves_valid_rows():
    csv_text = (
        "timestamp,temp_c\n"
        "2026-01-01T10:00:00,4.5\n"
        ",\n"
        "2026-01-01T11:00:00,4.6\n"
        "garbage,garbage\n"
    )
    result = validate_temperature_csv(csv_text)
    assert result.status == "partial"
    assert len(result.valid_entries) == 2
    assert len(result.invalid_rows) == 2


def test_missing_required_columns():
    csv_text = "time,temperature\n2026-01-01T10:00:00,4.5\n"
    result = validate_temperature_csv(csv_text)
    assert result.status == "malformed"
    assert result.invalid_rows[0]["reason"] == "missing_required_columns"