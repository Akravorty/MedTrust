"""
services/ledger/recall.py

Recall simulation (Sections 14-17). This is evidence-driven only:
- We never invent hospitals, wards, pharmacies, recipients, dates, or
  quantities (Section 15).
- If the ledger doesn't contain distribution events for a batch, we raise
  RecallTraceIncompleteError rather than guessing.
- DEMO-RECALL must produce a deterministic distribution trail every run
  (Section 27) — no randomness anywhere in this file.

Distribution data ownership: the master doc doesn't yet define a
`DistributionEvent` schema or say which service records ward/pharmacy
movement (Person 6's doc mentions "simulated distribution data" without a
finalized contract). Rather than inventing a new shared schema field
silently, we treat DISTRIBUTION_EVENT as one more AuditEvent action type,
carried in the existing `payload` dict of an AuditEvent — payload:
{"location": "...", "sequence": N}. This needs zero changes to
shared/schemas.py or the AuditEvent contract; it's the smallest
backward-compatible option per Section 3's instructions. Flagging this
choice for the integration lead: if Person 6/Person 1/Person 2 want a
first-class DistributionEvent model instead, that's a schema addition to
propose in shared/schemas.py, not something I should invent alone.
"""

import sqlite3

from services.ledger.service import (
    ACTION_RECALL_SIMULATED,
    RecallTraceIncompleteError,
    log_event,
)
from services.ledger.chain import get_trace, verify_chain
from services.ledger.service import ChainIntegrityFailureError

ACTION_DISTRIBUTION_EVENT = "DISTRIBUTION_EVENT"


def _extract_distribution_path(events: list[sqlite3.Row]) -> list[str]:
    """
    Pull an ordered list of locations from recorded DISTRIBUTION_EVENT
    actions only. Returns [] if none exist — caller decides that means
    "Recall trace incomplete", we never fabricate a path here.
    """
    import json

    locations = []
    for e in events:
        if e["action"] == ACTION_DISTRIBUTION_EVENT:
            payload = json.loads(e["payload"])
            location = payload.get("location")
            if location:
                locations.append(location)
    return locations


def simulate_recall(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    triggered_by: str,
) -> dict:
    """
    Returns a dict matching the RecallNotice schema fields. Raises:
      - BatchNotFoundError (via get_trace returning nothing — handled by
        caller/router, see router.py)
      - ChainIntegrityFailureError if the trace can't be trusted
      - RecallTraceIncompleteError if there's no recorded distribution
        history to reconstruct
    """
    events = get_trace(conn, batch_id)
    if not events:
        raise RecallTraceIncompleteError()

    verification = verify_chain(conn, batch_id)
    if not verification.valid:
        raise ChainIntegrityFailureError(verification.explanation)

    affected_departments = _extract_distribution_path(events)
    if not affected_departments:
        raise RecallTraceIncompleteError()

    notice_text = _generate_notice_text(batch_id, affected_departments)

    from datetime import datetime, timezone

    triggered_at = datetime.now(timezone.utc)

    # Record the simulation itself (Section 17) — but the payload here
    # only contains recall metadata, never another DISTRIBUTION_EVENT or
    # RECALL_SIMULATED action, so this can't recurse into itself.
    log_event(
        conn,
        batch_id=batch_id,
        actor=triggered_by,
        action=ACTION_RECALL_SIMULATED,
        payload={
            "affected_departments": affected_departments,
            "source_trace_verified": verification.valid,
            "event_count_used": len(events),
        },
    )

    return {
        "batch_id": batch_id,
        "affected_departments": affected_departments,
        "recipients": affected_departments,  # no separate recipient list recorded yet; see note below
        "generated_notice_text": notice_text,
        "triggered_by": triggered_by,
        "triggered_at": triggered_at,
    }


def _generate_notice_text(batch_id: str, affected_departments: list[str]) -> str:
    """
    Deterministic, evidence-only notice text. No LLM call lives here: per
    Section 16, if the current architecture assigns LLM-drafted recall
    wording to Person 4 (agents), Person 3 should expose structured
    evidence, not duplicate the integration. This function is exactly that
    structured-evidence-to-text step in its simplest safe form; swapping
    it for a call to Person 4's endpoint (passing this same
    affected_departments list as the ONLY factual input) is a one-line
    change once that endpoint exists — see the comment at the bottom.
    """
    path = " -> ".join(affected_departments)
    return (
        f"RECALL NOTICE - Batch {batch_id}\n\n"
        f"A defect has been identified in batch {batch_id} after distribution. "
        f"Based on the verified audit trail, this batch was distributed through "
        f"the following locations, in order: {path}.\n\n"
        f"All listed locations should immediately quarantine remaining stock of "
        f"this batch pending further instructions."
    )


# Integration note for Person 4 (do not implement here):
# def _generate_notice_text_via_agent(batch_id, affected_departments):
#     response = requests.post(f"{AGENT_URL}/agent/qa", json={
#         "query": f"Draft an urgent recall notice for batch {batch_id}",
#         "context": {"affected_departments": affected_departments},
#     })
#     # Agent may only rephrase affected_departments — never allowed to add
#     # locations/recipients not present in that list.
#     return response.json()["answer"]
