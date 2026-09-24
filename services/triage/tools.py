"""
services/triage/tools.py

Grounding tools for the triage agent. Deliberately calls the DB directly
(not HTTP, unlike services/agents/tools.py) since this is one process for
the hackathon build — see that file's own docstring for the pattern this
would grow into if the services were ever split across real teammates'
machines.

Every tool returns plain dicts (JSON-serializable) so they can be handed
straight to Gemini as function_response payloads.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from shared.database import get_connection
from services.patients.service import get_patient, get_patient_timeline
from services.facilities.service import get_facility, next_level_up, find_nearest_at_level


def get_patient_profile(tool_input: dict) -> dict:
    patient_id = tool_input.get("patient_id", "")
    conn = get_connection()
    patient = get_patient(conn, patient_id)
    if patient is None:
        return {"error": f"No patient found with id {patient_id}"}
    age = patient.age
    return {
        "patient_id": patient.patient_id,
        "name": patient.name,
        "age": age,
        "gender": patient.gender,
        "village": patient.village,
        "risk_category": patient.risk_category.value,
        "home_facility_id": patient.home_facility_id,
        "is_child": age < 5,
        "is_elderly": age >= 60,
    }


def get_patient_history(tool_input: dict) -> dict:
    """Prior visits/triage/referral events for this patient — lets the
    agent notice patterns like 'this is the third visit this month' rather
    than triaging each symptom report as if it were the patient's first."""
    patient_id = tool_input.get("patient_id", "")
    conn = get_connection()
    try:
        events = get_patient_timeline(conn, patient_id)
    except Exception:
        return {"patient_id": patient_id, "events": [], "note": "No prior history."}
    return {"patient_id": patient_id, "event_count": len(events), "events": events[-10:]}


def get_facility_capacity(tool_input: dict) -> dict:
    """Lets the agent check whether the suggested facility can actually
    take the patient right now (beds, teleconsult availability) rather than
    recommending a level blindly."""
    facility_id = tool_input.get("facility_id", "")
    conn = get_connection()
    row = get_facility(conn, facility_id)
    if row is None:
        return {"error": f"No facility found with id {facility_id}"}
    return {
        "facility_id": row["facility_id"],
        "name": row["name"],
        "level": row["level"],
        "district": row["district"],
        "beds_total": row["beds_total"],
        "beds_occupied": row["beds_occupied"],
        "beds_available": row["beds_total"] - row["beds_occupied"],
        "has_teleconsult": bool(row["has_teleconsult"]),
        "next_level_up": next_level_up(row["level"]),
    }


def find_referral_target(tool_input: dict) -> dict:
    """Given a home facility and a target level, find the nearest facility
    at that level in the same district to refer the patient to."""
    from_facility_id = tool_input.get("from_facility_id", "")
    target_level = tool_input.get("target_level", "")
    conn = get_connection()
    home = get_facility(conn, from_facility_id)
    if home is None:
        return {"error": f"No facility found with id {from_facility_id}"}
    target = find_nearest_at_level(conn, home["district"], target_level)
    if target is None:
        return {"error": f"No {target_level} facility found in district {home['district']}"}
    return {
        "facility_id": target["facility_id"],
        "name": target["name"],
        "level": target["level"],
        "district": target["district"],
        "has_teleconsult": bool(target["has_teleconsult"]),
    }


_TOOL_DISPATCH = {
    "get_patient_profile": get_patient_profile,
    "get_patient_history": get_patient_history,
    "get_facility_capacity": get_facility_capacity,
    "find_referral_target": find_referral_target,
}


def execute_triage_tool(name: str, tool_input: dict) -> dict:
    fn = _TOOL_DISPATCH.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(tool_input)
    except Exception as exc:  # noqa: BLE001 — never let a tool crash the agent loop
        return {"error": f"Tool {name} failed: {exc}"}
