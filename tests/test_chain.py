import sqlite3

import pytest

from services.ledger.chain import append_event, ensure_schema, verify_chain
from services.ledger.hashing import GENESIS_HASH


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    yield c
    c.close()


def test_single_event_chain_starts_at_genesis(conn):
    event = append_event(conn, batch_id="B1", actor="tester", action="BATCH_CREATED", payload={"x": 1})
    assert event["prev_hash"] == GENESIS_HASH
    result = verify_chain(conn, "B1")
    assert result.valid
    assert result.total_events == 1


def test_multi_event_chain_links_correctly(conn):
    e1 = append_event(conn, batch_id="B1", actor="a", action="BATCH_CREATED", payload={"x": 1})
    e2 = append_event(conn, batch_id="B1", actor="a", action="STATUS_CHANGED", payload={"x": 2})
    e3 = append_event(conn, batch_id="B1", actor="a", action="RISK_EVALUATED", payload={"x": 3})

    assert e2["prev_hash"] == e1["this_hash"]
    assert e3["prev_hash"] == e2["this_hash"]

    result = verify_chain(conn, "B1")
    assert result.valid
    assert result.total_events == 3


def test_verify_chain_empty_batch(conn):
    result = verify_chain(conn, "does-not-exist")
    assert result.valid  # vacuously — no events to fail
    assert result.total_events == 0


def _tamper(conn, event_id, column, value):
    conn.execute(f"UPDATE audit_events SET {column} = ? WHERE event_id = ?", (value, event_id))
    conn.commit()


def test_tamper_detects_modified_payload(conn):
    e1 = append_event(conn, batch_id="B1", actor="a", action="BATCH_CREATED", payload={"x": 1})
    append_event(conn, batch_id="B1", actor="a", action="STATUS_CHANGED", payload={"x": 2})

    assert verify_chain(conn, "B1").valid

    _tamper(conn, e1["event_id"], "payload", '{"x":999}')
    result = verify_chain(conn, "B1")
    assert not result.valid
    assert result.failure_type == "payload_hash_mismatch"
    assert result.failed_event_id == e1["event_id"]


def test_tamper_detects_modified_payload_hash(conn):
    e1 = append_event(conn, batch_id="B1", actor="a", action="BATCH_CREATED", payload={"x": 1})

    _tamper(conn, e1["event_id"], "payload_hash", "f" * 64)
    result = verify_chain(conn, "B1")
    assert not result.valid
    assert result.failure_type == "payload_hash_mismatch"


def test_tamper_detects_modified_this_hash(conn):
    e1 = append_event(conn, batch_id="B1", actor="a", action="BATCH_CREATED", payload={"x": 1})
    append_event(conn, batch_id="B1", actor="a", action="STATUS_CHANGED", payload={"x": 2})

    _tamper(conn, e1["event_id"], "this_hash", "e" * 64)
    result = verify_chain(conn, "B1")
    assert not result.valid
    # First check hit depends on position: event 1's own this_hash mismatch
    # is caught directly, OR (if checked in order) event 2's prev_hash
    # mismatch fires first since it now expects the tampered this_hash's
    # original value. Either failure type is a correct, real detection.
    assert result.failure_type in ("this_hash_mismatch", "prev_hash_mismatch")


def test_tamper_detects_modified_prev_hash(conn):
    append_event(conn, batch_id="B1", actor="a", action="BATCH_CREATED", payload={"x": 1})
    e2 = append_event(conn, batch_id="B1", actor="a", action="STATUS_CHANGED", payload={"x": 2})

    _tamper(conn, e2["event_id"], "prev_hash", "d" * 64)
    result = verify_chain(conn, "B1")
    assert not result.valid
    assert result.failure_type == "prev_hash_mismatch"
    assert result.failed_event_id == e2["event_id"]


def test_tamper_detects_deleted_event(conn):
    e1 = append_event(conn, batch_id="B1", actor="a", action="BATCH_CREATED", payload={"x": 1})
    append_event(conn, batch_id="B1", actor="a", action="STATUS_CHANGED", payload={"x": 2})

    conn.execute("DELETE FROM audit_events WHERE event_id = ?", (e1["event_id"],))
    conn.commit()

    result = verify_chain(conn, "B1")
    # With event 1 gone, event 2 is now "first" and its prev_hash no longer
    # equals GENESIS_HASH -> detected as a broken link.
    assert not result.valid
    assert result.failure_type == "prev_hash_mismatch"


def test_tamper_detects_reordered_events(conn):
    e1 = append_event(conn, batch_id="B1", actor="a", action="BATCH_CREATED", payload={"x": 1})
    e2 = append_event(conn, batch_id="B1", actor="a", action="STATUS_CHANGED", payload={"x": 2})

    # Swap sequence numbers to simulate reordering.
    conn.execute("UPDATE audit_events SET seq = 100 WHERE event_id = ?", (e1["event_id"],))
    conn.execute("UPDATE audit_events SET seq = 0 WHERE event_id = ?", (e2["event_id"],))
    conn.commit()

    result = verify_chain(conn, "B1")
    assert not result.valid


def test_explainer_is_generated_not_hardcoded(conn):
    append_event(conn, batch_id="B1", actor="a", action="BATCH_CREATED", payload={"x": 1})
    valid_result = verify_chain(conn, "B1")
    assert "verified" in valid_result.explanation.lower()

    e2 = append_event(conn, batch_id="B1", actor="a", action="STATUS_CHANGED", payload={"x": 2})
    _tamper(conn, e2["event_id"], "this_hash", "a" * 64)
    invalid_result = verify_chain(conn, "B1")
    assert "compromised" in invalid_result.explanation.lower()
    assert invalid_result.explanation != valid_result.explanation