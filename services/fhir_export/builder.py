"""
services/fhir_export/builder.py

Builds a FHIR R4 `collection` Bundle for one patient from what the care-access
module already stores: the Patient, the facilities they touch (Organization and,
when coordinates exist, Location), their referrals (ServiceRequest) and their
teleconsult sessions (Encounter, class VR).

Scope, stated plainly:
  * This is an EXPORT in a standard format. It is not ABHA/ABDM integration:
    there is no ABHA number, no consent manager, no gateway or registry
    connection, and the output has not been submitted to any ABDM profile
    validation.
  * Only the resources listed above are produced. Triage results, follow-ups,
    queue tickets and teleconsult notes are not exported.
  * The patient's age is stored, but a date of birth is not, so Patient.birthDate
    is left out rather than invented.
Custom fields use `urn:medtrust:` extension URLs, which FHIR consumers may ignore.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.facilities.service import get_facility
from services.patients.service import get_patient_or_raise
from services.referrals.service import list_referrals
from services.teleconsult.service import list_sessions_for_patient
from shared.schemas import ReferralStatus, UrgencyBand

FHIR_BASE = os.environ.get("FHIR_BASE_URL", "http://localhost:8000/fhir").rstrip("/")

ID_SYSTEM_PATIENT = "urn:medtrust:patient-id"
ID_SYSTEM_FACILITY = "urn:medtrust:facility-id"
EXT_RISK_CATEGORY = "urn:medtrust:ext:risk-category"
EXT_REFERRING_FACILITY = "urn:medtrust:ext:referring-facility"
EXT_REFERRAL_STATUS = "urn:medtrust:ext:referral-status"
EXT_FACILITY_LEVEL = "urn:medtrust:ext:facility-level"

_GENDER = {"f": "female", "female": "female", "m": "male", "male": "male", "o": "other", "other": "other"}

# MedTrust referral states -> FHIR ServiceRequest.status. The original state is
# also carried in an extension because FHIR has no IN_TRANSIT / ACCEPTED.
_REFERRAL_STATUS = {
    ReferralStatus.CREATED: "active",
    ReferralStatus.ACCEPTED: "active",
    ReferralStatus.IN_TRANSIT: "active",
    ReferralStatus.COMPLETED: "completed",
    ReferralStatus.CANCELLED: "revoked",
}
_URGENCY_TO_PRIORITY = {
    UrgencyBand.ROUTINE: "routine",
    UrgencyBand.SOON: "urgent",
    UrgencyBand.URGENT: "asap",
    UrgencyBand.EMERGENCY: "stat",
}
_ENCOUNTER_STATUS = {"SCHEDULED": "planned", "ACTIVE": "in-progress", "COMPLETED": "finished"}


def _dt(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _ref(resource_type: str, resource_id: str) -> Dict[str, str]:
    return {"reference": f"{resource_type}/{resource_id}"}


def _entry(resource: Dict[str, Any]) -> Dict[str, Any]:
    return {"fullUrl": f"{FHIR_BASE}/{resource['resourceType']}/{resource['id']}", "resource": resource}


def _organization(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "resourceType": "Organization",
        "id": row["facility_id"],
        "identifier": [{"system": ID_SYSTEM_FACILITY, "value": row["facility_id"]}],
        "active": True,
        "type": [{
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/organization-type",
                "code": "prov",
                "display": "Healthcare Provider",
            }],
            "text": row["level"],
        }],
        "name": row["name"],
        "address": [{"text": row["village_or_area"], "district": row["district"], "country": "IN"}],
        "extension": [{"url": EXT_FACILITY_LEVEL, "valueCode": row["level"]}],
    }


def _location(row: sqlite3.Row) -> Optional[Dict[str, Any]]:
    if row["latitude"] is None or row["longitude"] is None:
        return None
    return {
        "resourceType": "Location",
        "id": row["facility_id"],
        "status": "active",
        "name": row["name"],
        "position": {"latitude": row["latitude"], "longitude": row["longitude"]},
        "managingOrganization": _ref("Organization", row["facility_id"]),
    }


def build_patient_bundle(conn: sqlite3.Connection, patient_id: str) -> Dict[str, Any]:
    """FHIR R4 Bundle (type=collection) for one patient. Raises PatientNotFoundError if unknown."""
    patient = get_patient_or_raise(conn, patient_id)
    referrals = list_referrals(conn, patient_id=patient_id)
    sessions = list_sessions_for_patient(conn, patient_id)

    # Every facility the record points at, once each, in first-seen order.
    facility_ids: List[str] = []
    for fid in (
        [patient.home_facility_id]
        + [f for r in referrals for f in (r.from_facility_id, r.to_facility_id)]
        + [s.facility_id for s in sessions]
    ):
        if fid not in facility_ids:
            facility_ids.append(fid)

    entries: List[Dict[str, Any]] = []
    known_facilities = set()
    for fid in facility_ids:
        row = get_facility(conn, fid)
        if row is None:
            continue  # a dangling id must not make the whole export fail
        known_facilities.add(fid)
        entries.append(_entry(_organization(row)))
        location = _location(row)
        if location is not None:
            entries.append(_entry(location))

    patient_res: Dict[str, Any] = {
        "resourceType": "Patient",
        "id": patient.patient_id,
        "identifier": [{"system": ID_SYSTEM_PATIENT, "value": patient.patient_id}],
        "active": True,
        "name": [{"text": patient.name}],
        "gender": _GENDER.get(str(patient.gender).strip().lower(), "unknown"),
        "address": [{"text": patient.village, "country": "IN"}],
        "extension": [{"url": EXT_RISK_CATEGORY, "valueCode": patient.risk_category.value}],
    }
    if patient.phone:
        patient_res["telecom"] = [{"system": "phone", "value": patient.phone}]
    if patient.home_facility_id in known_facilities:
        patient_res["managingOrganization"] = _ref("Organization", patient.home_facility_id)
    entries.insert(0, _entry(patient_res))

    referral_ids = set()
    for r in referrals:
        referral_ids.add(r.referral_id)
        request: Dict[str, Any] = {
            "resourceType": "ServiceRequest",
            "id": r.referral_id,
            "identifier": [{"system": "urn:medtrust:referral-id", "value": r.referral_id}],
            "status": _REFERRAL_STATUS[r.status],
            "intent": "order",
            "priority": _URGENCY_TO_PRIORITY[r.urgency],
            "subject": _ref("Patient", patient.patient_id),
            "authoredOn": _dt(r.created_at),
            "requester": {"display": r.created_by},
            "reasonCode": [{"text": r.reason}],
            "extension": [{"url": EXT_REFERRAL_STATUS, "valueCode": r.status.value}],
        }
        if r.to_facility_id in known_facilities:
            request["performer"] = [_ref("Organization", r.to_facility_id)]
        if r.from_facility_id in known_facilities:
            request["extension"].append(
                {"url": EXT_REFERRING_FACILITY, "valueReference": _ref("Organization", r.from_facility_id)}
            )
        entries.append(_entry(request))

    for s in sessions:
        encounter: Dict[str, Any] = {
            "resourceType": "Encounter",
            "id": s.session_id,
            "identifier": [{"system": "urn:medtrust:teleconsult-session-id", "value": s.session_id}],
            "status": _ENCOUNTER_STATUS.get(s.status, "unknown"),
            "class": {
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": "VR",
                "display": "virtual",
            },
            "subject": _ref("Patient", patient.patient_id),
            "participant": [{"individual": {"display": s.doctor_name}}],
        }
        period = {k: v for k, v in (("start", _dt(s.started_at)), ("end", _dt(s.ended_at))) if v}
        if period:
            encounter["period"] = period
        if s.facility_id in known_facilities:
            encounter["serviceProvider"] = _ref("Organization", s.facility_id)
        if s.referral_id and s.referral_id in referral_ids:
            encounter["basedOn"] = [_ref("ServiceRequest", s.referral_id)]
        entries.append(_entry(encounter))

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "timestamp": _dt(datetime.now(timezone.utc)),
        "entry": entries,
    }
