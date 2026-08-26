from services.ledger.hashing import (
    GENESIS_HASH,
    compute_event_hash,
    compute_payload_hash,
    canonicalize,
)


def test_genesis_hash_is_64_zero_chars():
    assert GENESIS_HASH == "0" * 64
    assert len(GENESIS_HASH) == 64


def test_canonicalize_ignores_key_order():
    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}
    assert canonicalize(a) == canonicalize(b)


def test_payload_hash_deterministic_same_input():
    payload = {"medicine_name": "Paracetamol", "batch_number": "B123"}
    h1 = compute_payload_hash(payload)
    h2 = compute_payload_hash(dict(reversed(list(payload.items()))))
    assert h1 == h2


def test_payload_hash_changes_when_payload_changes():
    h1 = compute_payload_hash({"x": 1})
    h2 = compute_payload_hash({"x": 2})
    assert h1 != h2


def test_event_hash_deterministic():
    kwargs = dict(
        event_id="evt-1",
        batch_id="batch-1",
        actor="tester",
        action="BATCH_CREATED",
        payload_hash="abc123",
        prev_hash=GENESIS_HASH,
        timestamp="2026-01-01T00:00:00+00:00",
    )
    assert compute_event_hash(**kwargs) == compute_event_hash(**kwargs)


def test_event_hash_changes_when_prev_hash_changes():
    base = dict(
        event_id="evt-1",
        batch_id="batch-1",
        actor="tester",
        action="BATCH_CREATED",
        payload_hash="abc123",
        timestamp="2026-01-01T00:00:00+00:00",
    )
    h1 = compute_event_hash(prev_hash=GENESIS_HASH, **base)
    h2 = compute_event_hash(prev_hash="1" * 64, **base)
    assert h1 != h2