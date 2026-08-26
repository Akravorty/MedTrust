"""
services/agents/tools.py

Person 4 (Agentic Layer) — Tool layer for the QA / Supplier agents.

Responsibilities:
    1. Define Pydantic-typed tool input schemas + Anthropic tool-call JSON schemas
       for: get_batch, get_decision, get_trace, get_supplier_history.
    2. Implement "dynamic teammate routing": call live services (Person 1/2/3)
       when their env-configured URLs are reachable, and fall back silently and
       seamlessly to in-memory mock data when they are absent or erroring —
       never raising, never leaking a stack trace to the agent loop.
    3. Expose a single `execute_tool(name, tool_input)` dispatcher that
       qa_agent.py / supplier_agent.py drive from their tool-execution loop.

No placeholders. No TODOs. Depends only on: httpx, pydantic (v2), shared.schemas.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError

from shared.schemas import (
    Batch,
    BatchStatus,
    RiskDecision,
    Supplier,
    TempLogEntry,
)

logger = logging.getLogger("meditrust.agents.tools")

# --------------------------------------------------------------------------- #
# Environment-driven teammate routing configuration
# --------------------------------------------------------------------------- #

INTAKE_SERVICE_URL = os.environ.get("INTAKE_SERVICE_URL", "").rstrip("/")
RISK_SERVICE_URL = os.environ.get("RISK_SERVICE_URL", "").rstrip("/")
LEDGER_SERVICE_URL = os.environ.get("LEDGER_SERVICE_URL", "").rstrip("/")

# Suppliers currently have no dedicated Person in the master doc's env-var
# list; SUPPLIER_SERVICE_URL is supported optionally for forward-compat but
# is not required — absence always falls back to the local mock.
SUPPLIER_SERVICE_URL = os.environ.get("SUPPLIER_SERVICE_URL", "").rstrip("/")

_HTTP_TIMEOUT = httpx.Timeout(connect=2.0, read=4.0, write=2.0, pool=2.0)


# --------------------------------------------------------------------------- #
# Pydantic tool-input schemas
# --------------------------------------------------------------------------- #

class GetBatchInput(BaseModel):
    batch_id: str = Field(..., description="Unique batch identifier, e.g. 'DEMO-HOLD'.")


class GetDecisionInput(BaseModel):
    batch_id: str = Field(..., description="Batch identifier to fetch the risk decision for.")


class GetTraceInput(BaseModel):
    batch_id: str = Field(..., description="Batch identifier to fetch the immutable ledger trace for.")


class GetSupplierHistoryInput(BaseModel):
    supplier_id: str = Field(..., description="Supplier identifier, e.g. 'SUP-001'.")


# --------------------------------------------------------------------------- #
# Anthropic tool-call definitions (Claude Messages API `tools` param)
# --------------------------------------------------------------------------- #

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "get_batch",
        "description": (
            "Fetch the full intake record for a medicine batch: identifiers, OCR/QR "
            "match data, manufacture/expiry dates, storage temperature log, physical "
            "inspection notes, and current status. Use this to ground any claim about "
            "what happened to a specific batch."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "batch_id": {
                    "type": "string",
                    "description": "Unique batch identifier, e.g. 'DEMO-HOLD'.",
                }
            },
            "required": ["batch_id"],
        },
    },
    {
        "name": "get_decision",
        "description": (
            "Fetch the risk engine's decision for a batch: risk score, ACCEPT/HOLD/REJECT "
            "decision, the rule that triggered it, SHAP feature contributors, and the "
            "human-readable reasons. Use this to ground claims about why a batch was "
            "accepted, held, or rejected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "batch_id": {
                    "type": "string",
                    "description": "Batch identifier to fetch the risk decision for.",
                }
            },
            "required": ["batch_id"],
        },
    },
    {
        "name": "get_trace",
        "description": (
            "Fetch the hash-chained audit trail (ledger) for a batch: every recorded "
            "action, actor, and timestamp, with prev_hash/this_hash linkage. Use this to "
            "ground claims about the batch's custody or processing history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "batch_id": {
                    "type": "string",
                    "description": "Batch identifier to fetch the immutable ledger trace for.",
                }
            },
            "required": ["batch_id"],
        },
    },
    {
        "name": "get_supplier_history",
        "description": (
            "Fetch a supplier's quality record: 3-month and 6-month reject rates, total "
            "batches supplied, and flagged incidents. Use this to ground claims about a "
            "supplier's reliability or trend."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "supplier_id": {
                    "type": "string",
                    "description": "Supplier identifier, e.g. 'SUP-001'.",
                }
            },
            "required": ["supplier_id"],
        },
    },
]


# --------------------------------------------------------------------------- #
# Mock data (used whenever a live teammate service is absent or unreachable)
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mock_batches() -> Dict[str, Batch]:
    hold_received = _now() - timedelta(hours=6)
    accept_received = _now() - timedelta(hours=3)

    hold_batch = Batch(
        batch_id="DEMO-HOLD",
        medicine_name="Amoxicillin 500mg",
        batch_number="AMX-2026-0417",
        supplier_id="SUP-001",
        qr_payload="MEDITRUST|AMX-2026-0417|SUP-001|2026-04-17",
        ocr_extracted_text={
            "raw_text": "AMOXICILLIN 500MG BATCH AMX-2026-0417 MFG 2026-01-10 EXP 2027-01-10",
            "medicine_name": "Amoxicillin 500mg",
            "batch_number": "AMX-2026-0417",
            "manufacture_date": "2026-01-10",
            "expiry_date": "2027-01-10",
        },
        ocr_qr_match_score=0.94,
        manufacture_date=datetime(2026, 1, 10, tzinfo=timezone.utc),
        expiry_date=datetime(2027, 1, 10, tzinfo=timezone.utc),
        received_timestamp=hold_received,
        storage_temp_log=[
            TempLogEntry(timestamp=hold_received, temp_c=6.1),
            TempLogEntry(timestamp=hold_received + timedelta(hours=1), temp_c=5.9),
            TempLogEntry(timestamp=hold_received + timedelta(hours=2), temp_c=11.4),
            TempLogEntry(timestamp=hold_received + timedelta(hours=3), temp_c=12.8),
            TempLogEntry(timestamp=hold_received + timedelta(hours=4), temp_c=7.2),
        ],
        physical_inspection_notes="Outer carton slightly crushed at one corner; blister strips intact.",
        status=BatchStatus.HOLD,
    )

    accept_batch = Batch(
        batch_id="DEMO-ACCEPT",
        medicine_name="Paracetamol 650mg",
        batch_number="PCM-2026-0912",
        supplier_id="SUP-002",
        qr_payload="MEDITRUST|PCM-2026-0912|SUP-002|2026-05-02",
        ocr_extracted_text={
            "raw_text": "PARACETAMOL 650MG BATCH PCM-2026-0912 MFG 2026-02-01 EXP 2028-02-01",
            "medicine_name": "Paracetamol 650mg",
            "batch_number": "PCM-2026-0912",
            "manufacture_date": "2026-02-01",
            "expiry_date": "2028-02-01",
        },
        ocr_qr_match_score=0.99,
        manufacture_date=datetime(2026, 2, 1, tzinfo=timezone.utc),
        expiry_date=datetime(2028, 2, 1, tzinfo=timezone.utc),
        received_timestamp=accept_received,
        storage_temp_log=[
            TempLogEntry(timestamp=accept_received, temp_c=4.8),
            TempLogEntry(timestamp=accept_received + timedelta(hours=1), temp_c=5.0),
            TempLogEntry(timestamp=accept_received + timedelta(hours=2), temp_c=4.9),
        ],
        physical_inspection_notes="No visible damage. Packaging intact.",
        status=BatchStatus.ACCEPTED,
    )

    return {b.batch_id: b for b in (hold_batch, accept_batch)}


def _mock_decisions() -> Dict[str, RiskDecision]:
    hold_decision = RiskDecision(
        batch_id="DEMO-HOLD",
        risk_score=0.78,
        decision="HOLD",
        triggered_rule="COLD_CHAIN_EXCURSION_GT_10C",
        shap_contributors=[
            {
                "feature": "storage_temp_log.max_excursion_c",
                "display_label": "Temperature excursion (12.8°C)",
                "contribution": 0.41,
                "direction": "increases_risk",
            },
            {
                "feature": "supplier.reject_rate_3mo",
                "display_label": "Supplier's rising 3-month reject rate",
                "contribution": 0.22,
                "direction": "increases_risk",
            },
            {
                "feature": "physical_inspection.damage_flag",
                "display_label": "Minor carton damage noted on inspection",
                "contribution": 0.09,
                "direction": "increases_risk",
            },
        ],
        reasons=[
            "Temperature log shows two consecutive readings above the 10C cold-chain ceiling.",
            "Supplying vendor SUP-001 has an elevated 3-month reject rate.",
            "Physical inspection noted minor carton damage.",
        ],
        decided_at=_now() - timedelta(hours=5, minutes=40),
        model_version="risk-engine-v1.3.2",
    )

    accept_decision = RiskDecision(
        batch_id="DEMO-ACCEPT",
        risk_score=0.11,
        decision="ACCEPT",
        triggered_rule="NONE",
        shap_contributors=[
            {
                "feature": "storage_temp_log.max_excursion_c",
                "display_label": "Temperature stayed within cold-chain range",
                "contribution": 0.02,
                "direction": "decreases_risk",
            },
            {
                "feature": "supplier.reject_rate_3mo",
                "display_label": "Supplier has a clean recent record",
                "contribution": 0.01,
                "direction": "decreases_risk",
            },
        ],
        reasons=["All checks within tolerance; no cold-chain excursion; supplier record clean."],
        decided_at=_now() - timedelta(hours=2, minutes=50),
        model_version="risk-engine-v1.3.2",
    )

    return {d.batch_id: d for d in (hold_decision, accept_decision)}


def _mock_traces() -> Dict[str, List[Dict[str, Any]]]:
    base = _now() - timedelta(hours=6)

    def _event(batch_id: str, seq: int, actor: str, action: str, prev_hash: str) -> Dict[str, Any]:
        this_hash = f"sha256:{batch_id.lower()}-evt{seq:03d}"
        return {
            "event_id": f"{batch_id}-EVT-{seq:03d}",
            "batch_id": batch_id,
            "actor": actor,
            "action": action,
            "payload_hash": f"sha256:payload-{batch_id.lower()}-{seq:03d}",
            "prev_hash": prev_hash,
            "this_hash": this_hash,
            "timestamp": (base + timedelta(minutes=15 * seq)).isoformat(),
        }

    hold_events = []
    prev = "sha256:genesis"
    for i, (actor, action) in enumerate(
        [
            ("intake-service", "BATCH_RECEIVED"),
            ("ocr-service", "QR_OCR_MATCHED"),
            ("iot-gateway", "TEMP_EXCURSION_DETECTED"),
            ("risk-engine", "DECISION_HOLD_ISSUED"),
            ("qa-officer:priya.sharma", "MANUAL_REVIEW_OPENED"),
        ],
        start=1,
    ):
        evt = _event("DEMO-HOLD", i, actor, action, prev)
        hold_events.append(evt)
        prev = evt["this_hash"]

    accept_events = []
    prev = "sha256:genesis"
    for i, (actor, action) in enumerate(
        [
            ("intake-service", "BATCH_RECEIVED"),
            ("ocr-service", "QR_OCR_MATCHED"),
            ("risk-engine", "DECISION_ACCEPT_ISSUED"),
        ],
        start=1,
    ):
        evt = _event("DEMO-ACCEPT", i, actor, action, prev)
        accept_events.append(evt)
        prev = evt["this_hash"]

    return {"DEMO-HOLD": hold_events, "DEMO-ACCEPT": accept_events}


def _mock_suppliers() -> Dict[str, Supplier]:
    sup1 = Supplier(
        supplier_id="SUP-001",
        name="Ashirwad Pharma Distributors",
        reject_rate_3mo=0.14,
        reject_rate_6mo=0.06,
        total_batches_supplied=212,
        flagged_incidents=["2026-03-02: cold-chain excursion", "2026-04-17: cold-chain excursion (this batch)"],
    )
    sup2 = Supplier(
        supplier_id="SUP-002",
        name="Nilkanth Medisupply Pvt Ltd",
        reject_rate_3mo=0.02,
        reject_rate_6mo=0.03,
        total_batches_supplied=340,
        flagged_incidents=[],
    )
    return {s.supplier_id: s for s in (sup1, sup2)}


_MOCK_BATCHES = _mock_batches()
_MOCK_DECISIONS = _mock_decisions()
_MOCK_TRACES = _mock_traces()
_MOCK_SUPPLIERS = _mock_suppliers()


# --------------------------------------------------------------------------- #
# Generic "call live service, fall back to mock" helper
# --------------------------------------------------------------------------- #

def _fetch_live(base_url: str, path: str) -> Optional[Dict[str, Any]]:
    """
    Attempt a GET against a live teammate service.

    Returns the parsed JSON body on success (HTTP 2xx), or None on ANY failure
    (missing base_url, connection error, timeout, non-2xx status, malformed
    JSON). Never raises — callers always have a mock fallback ready.
    """
    if not base_url:
        return None

    url = f"{base_url}{path}"
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            response = client.get(url)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Teammate service returned HTTP %s for %s — falling back to mock.",
            exc.response.status_code,
            url,
        )
        return None
    except httpx.RequestError as exc:
        logger.warning("Teammate service unreachable at %s (%s) — falling back to mock.", url, exc)
        return None
    except (ValueError, TypeError) as exc:
        logger.warning("Teammate service returned malformed JSON from %s (%s) — falling back to mock.", url, exc)
        return None


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #

def get_batch(batch_id: str) -> Dict[str, Any]:
    live = _fetch_live(INTAKE_SERVICE_URL, f"/intake/batches/{batch_id}")
    if live is not None:
        try:
            return Batch.model_validate(live).model_dump(mode="json")
        except ValidationError as exc:
            logger.warning("Live batch payload failed schema validation (%s) — falling back to mock.", exc)

    batch = _MOCK_BATCHES.get(batch_id)
    if batch is None:
        return {"error": f"No batch found with id '{batch_id}'.", "source": "mock"}
    return {**batch.model_dump(mode="json"), "_source": "mock"}


def get_decision(batch_id: str) -> Dict[str, Any]:
    live = _fetch_live(RISK_SERVICE_URL, f"/risk/decisions/{batch_id}")
    if live is not None:
        try:
            return RiskDecision.model_validate(live).model_dump(mode="json")
        except ValidationError as exc:
            logger.warning("Live decision payload failed schema validation (%s) — falling back to mock.", exc)

    decision = _MOCK_DECISIONS.get(batch_id)
    if decision is None:
        return {"error": f"No risk decision found for batch '{batch_id}'.", "source": "mock"}
    return {**decision.model_dump(mode="json"), "_source": "mock"}


def get_trace(batch_id: str) -> Dict[str, Any]:
    live = _fetch_live(LEDGER_SERVICE_URL, f"/ledger/trace/{batch_id}")
    if live is not None:
        return {"batch_id": batch_id, "events": live, "_source": "live"}

    events = _MOCK_TRACES.get(batch_id)
    if events is None:
        return {"error": f"No ledger trace found for batch '{batch_id}'.", "source": "mock"}
    return {"batch_id": batch_id, "events": events, "_source": "mock"}


def get_supplier_history(supplier_id: str) -> Dict[str, Any]:
    live = _fetch_live(SUPPLIER_SERVICE_URL, f"/suppliers/{supplier_id}")
    if live is not None:
        try:
            return Supplier.model_validate(live).model_dump(mode="json")
        except ValidationError as exc:
            logger.warning("Live supplier payload failed schema validation (%s) — falling back to mock.", exc)

    supplier = _MOCK_SUPPLIERS.get(supplier_id)
    if supplier is None:
        return {"error": f"No supplier found with id '{supplier_id}'.", "source": "mock"}
    return {**supplier.model_dump(mode="json"), "_source": "mock"}


# --------------------------------------------------------------------------- #
# Dispatch table used by the agent tool-execution loop
# --------------------------------------------------------------------------- #

_INPUT_MODELS: Dict[str, type[BaseModel]] = {
    "get_batch": GetBatchInput,
    "get_decision": GetDecisionInput,
    "get_trace": GetTraceInput,
    "get_supplier_history": GetSupplierHistoryInput,
}

_TOOL_FUNCTIONS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "get_batch": get_batch,
    "get_decision": get_decision,
    "get_trace": get_trace,
    "get_supplier_history": get_supplier_history,
}


def execute_tool(name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate `tool_input` against the tool's Pydantic schema and execute it.

    Always returns a JSON-serializable dict — either the tool's result or a
    structured `{"error": ...}` payload — and never raises, so the agent's
    tool-execution loop can feed the result straight back to Claude as a
    tool_result block regardless of outcome.
    """
    input_model = _INPUT_MODELS.get(name)
    func = _TOOL_FUNCTIONS.get(name)

    if input_model is None or func is None:
        return {"error": f"Unknown tool '{name}'."}

    try:
        validated = input_model.model_validate(tool_input)
    except ValidationError as exc:
        return {"error": f"Invalid input for tool '{name}': {exc.errors()}"}
