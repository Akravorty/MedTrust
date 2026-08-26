import sqlite3

import pytest

from services.ledger.chain import append_event, ensure_schema
from services.ledger.recall import ACTION_DISTRIBUTION_EVENT, simulate_recall
from services.ledger.service import RecallTraceIncompleteError


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    yield c
    c.close()


DEMO_RECALL_PATH = ["Central Store", "Pharmacy Store B", "Ward 3", "Ward 7"]


def _seed_demo_recall(conn, batch_id="DEMO-RECALL"):
    """
    Deterministic distribution history (Section 27) — same structure every
    run, no randomness. Matches the master doc's Section 7 example path.
    """
    append_event(conn, batch_id=batch_id, actor="system", action="BATCH_CREATED", payload={})
    for i, location in enumerate(DEMO_RECALL_PATH):
        append_event(
            conn,
            batch_id=batch_id,
            actor="system",
            action=ACTION_DISTRIBUTION_EVENT,
            payload={"location": location, "sequence": i},
        )


def test_recall_with_valid_distribution_trace(conn):
    _seed_demo_recall(conn, "B1")
    result = simulate_recall(conn, batch_id="B1", triggered_by="quality_officer")
    assert result["affected_departments"] == DEMO_RECALL_PATH
    assert "B1" in result["generated_notice_text"]
    for location in DEMO_RECALL_PATH:
        assert location in result["generated_notice_text"]


def test_demo_recall_is_deterministic_across_runs(conn):
    _seed_demo_recall(conn, "DEMO-RECALL")
    result1 = simulate_recall(conn, batch_id="DEMO-RECALL", triggered_by="x")

    conn2 = sqlite3.connect(":memory:")
    conn2.row_factory = sqlite3.Row
    ensure_schema(conn2)
    _seed_demo_recall(conn2, "DEMO-RECALL")
    result2 = simulate_recall(conn2, batch_id="DEMO-RECALL", triggered_by="x")

    assert result1["affected_departments"] == result2["affected_departments"]
    conn2.close()


def test_recall_missing_distribution_history_raises_incomplete(conn):
    append_event(conn, batch_id="B2", actor="system", action="BATCH_CREATED", payload={})
    with pytest.raises(RecallTraceIncompleteError):
        simulate_recall(conn, batch_id="B2", triggered_by="x")


def test_recall_nonexistent_batch_raises_incomplete(conn):
    with pytest.raises(RecallTraceIncompleteError):
        simulate_recall(conn, batch_id="does-not-exist", triggered_by="x")


def test_recall_never_fabricates_locations_beyond_recorded(conn):
    partial_path = ["Central Store", "Pharmacy Store B"]
    append_event(conn, batch_id="B3", actor="system", action="BATCH_CREATED", payload={})
    for i, loc in enumerate(partial_path):
        append_event(
            conn, batch_id="B3", actor="system", action=ACTION_DISTRIBUTION_EVENT,
            payload={"location": loc, "sequence": i},
        )
    result = simulate_recall(conn, batch_id="B3", triggered_by="x")
    assert result["affected_departments"] == partial_path  # exactly what was recorded, nothing more


def test_recall_simulation_is_itself_audited(conn):
    _seed_demo_recall(conn, "B4")
    simulate_recall(conn, batch_id="B4", triggered_by="quality_officer")

    from services.ledger.chain import get_trace

    events = get_trace(conn, "B4")
    recall_events = [e for e in events if e["action"] == "RECALL_SIMULATED"]
    assert len(recall_events) == 1
    assert recall_events[0]["actor"] == "quality_officer"