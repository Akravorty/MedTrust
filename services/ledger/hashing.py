"""
services/ledger/hashing.py

Canonical, deterministic SHA-256 hashing for ledger events.

Two layers of integrity (see integration master doc / Person 3 doc):

  1. payload_hash  — proves the event's payload matches what was recorded.
  2. this_hash     — binds the event into the chain (payload_hash + prev_hash).

We never hash str(dict) or an arbitrary repr — dict ordering /
representation must not be part of the integrity contract. Instead we use
json.dumps with sort_keys=True and a fixed separator style, which gives a
stable byte string for equivalent data regardless of key insertion order.
"""

import hashlib
import json
from typing import Any

GENESIS_HASH = "0" * 64


def canonicalize(data: dict[str, Any]) -> str:
    """
    Produce a stable, deterministic string representation of `data`.
    - sort_keys=True: key ordering never affects the result.
    - separators=(",", ":"): no incidental whitespace differences.
    - default=str: dates/datetimes/enums serialize consistently instead of
      raising or depending on caller pre-formatting.
    """
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def compute_payload_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over the canonicalized event payload only (Layer 1)."""
    canonical = canonicalize(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_event_hash(
    *,
    event_id: str,
    batch_id: str,
    actor: str,
    action: str,
    payload_hash: str,
    prev_hash: str,
    timestamp: str,
) -> str:
    """
    SHA-256 over the event's identifying fields + its payload_hash +
    prev_hash (Layer 2). Including prev_hash is what makes this a chain:
    changing any earlier event changes every hash after it.

    `timestamp` must be passed as an already-stable string (e.g.
    isoformat()) by the caller, since datetime objects aren't canonical on
    their own.
    """
    material = {
        "event_id": event_id,
        "batch_id": batch_id,
        "actor": actor,
        "action": action,
        "payload_hash": payload_hash,
        "prev_hash": prev_hash,
        "timestamp": timestamp,
    }
    canonical = canonicalize(material)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
