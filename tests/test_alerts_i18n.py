"""Alert wording: every covered language has all three alert types, the batch id
is appended, and languages without reviewed text fall back to English."""

from shared.alert_texts import ALERT_TEXTS, NOT_YET_COVERED
from services.alerts.service import _resolve_message

TYPES = ("HOLD", "REJECT", "RECALL")


def test_every_language_has_all_alert_types():
    for lang, texts in ALERT_TEXTS.items():
        for t in TYPES:
            assert texts.get(t, "").strip(), f"{lang} missing {t}"


def test_batch_id_is_appended():
    for lang in ALERT_TEXTS:
        assert _resolve_message("REJECT", lang, "B-1").endswith("[B-1]")


def test_case_insensitive_language_code():
    assert _resolve_message("HOLD", "HI", "B-1") == _resolve_message("HOLD", "hi", "B-1")


def test_uncovered_language_falls_back_to_english():
    for lang in NOT_YET_COVERED:
        msg = _resolve_message("REJECT", lang, "B-1")
        assert "REJECTED" in msg and "B-1" in msg