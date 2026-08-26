from services.intake.identity import compare_identity, STRONG_MATCH_THRESHOLD, UNCERTAIN_THRESHOLD


def test_exact_match():
    ocr = {"batch_number": "B123", "medicine_name": "Paracetamol"}
    qr = {"batch_number": "B123", "medicine_name": "Paracetamol"}
    result = compare_identity(ocr, qr)
    assert result.overall_score >= STRONG_MATCH_THRESHOLD
    assert result.mismatches == []
    assert result.band() == "strong_match"


def test_near_match_minor_ocr_typo_in_name_still_strong_if_batch_exact():
    ocr = {"batch_number": "B123", "medicine_name": "Paracetamol"}
    qr = {"batch_number": "B123", "medicine_name": "Paracetamol "}
    result = compare_identity(ocr, qr)
    assert result.overall_score >= STRONG_MATCH_THRESHOLD


def test_batch_number_mismatch_always_flagged_even_with_high_overall_score():
    # medicine_name matches perfectly, but batch_number differs - this must
    # ALWAYS surface as a mismatch, never hidden by a decent overall score.
    ocr = {"batch_number": "B123", "medicine_name": "Paracetamol"}
    qr = {"batch_number": "B999", "medicine_name": "Paracetamol"}
    result = compare_identity(ocr, qr)
    assert any("batch_number_mismatch" in m for m in result.mismatches)


def test_medicine_name_mismatch_detected():
    ocr = {"batch_number": "B123", "medicine_name": "Paracetamol"}
    qr = {"batch_number": "B123", "medicine_name": "Ibuprofen"}
    result = compare_identity(ocr, qr)
    assert any("medicine_name_mismatch" in m for m in result.mismatches)


def test_expiry_date_mismatch_detected():
    from datetime import date
    ocr = {"expiry_date": date(2027, 1, 1)}
    qr = {"expiry_date": date(2026, 1, 1)}
    result = compare_identity(ocr, qr)
    assert any("expiry_date_mismatch" in m for m in result.mismatches)


def test_supplier_mismatch_detected():
    ocr = {"supplier_id": "SUP-1"}
    qr = {"supplier_id": "SUP-2"}
    result = compare_identity(ocr, qr)
    assert any("supplier_id_mismatch" in m for m in result.mismatches)


def test_multiple_mismatches_all_reported():
    from datetime import date
    ocr = {"batch_number": "B123", "medicine_name": "Paracetamol", "expiry_date": date(2027, 1, 1), "supplier_id": "SUP-1"}
    qr = {"batch_number": "B999", "medicine_name": "Ibuprofen", "expiry_date": date(2026, 1, 1), "supplier_id": "SUP-2"}
    result = compare_identity(ocr, qr)
    assert len(result.mismatches) == 4
    assert result.band() == "mismatch"


def test_fields_present_only_on_one_side_are_not_compared():
    ocr = {"batch_number": "B123"}
    qr = {"medicine_name": "Paracetamol"}
    result = compare_identity(ocr, qr)
    assert result.fields_compared == []
    assert result.overall_score == 0.0
    assert result.mismatches == []  # nothing to compare, so nothing to flag


def test_uncertain_band_for_middling_score():
    # Slight but real difference in medicine name similarity, batch exact
    ocr = {"batch_number": "B123", "medicine_name": "Paracetamol Tablets"}
    qr = {"batch_number": "B123", "medicine_name": "Paracetamo Tabs"}
    result = compare_identity(ocr, qr)
    assert result.overall_score < 1.0
