"""
tests/test_fhir_export.py

FHIR R4 export of a patient's care-access record. Where the optional
`fhir.resources` package is installed (see requirements-dev.txt) the bundle is
validated against the R4B resource models, so structural mistakes fail here
instead of in front of a judge.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.facilities.service import insert_facility
from services.fhir_export import router as fhir_router
from services.fhir_export.builder import build_patient_bundle
from services.patients.service import PatientNotFoundError, register_patient
from services.referrals.service import create_referral, update_referral_status
from services.teleconsult.service import complete_session, schedule_session, start_session
from shared.schemas import Facility, FacilityLevel, ReferralStatus, RiskCategory, UrgencyBand


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    import shared.database as database_module

    monkeypatch.setattr(database_module, "DB_PATH", tmp_path / "fhir_test.db")
    monkeypatch.setattr(database_module, "_connection", None)
    database_module.init_db()
    c = database_module.get_connection()
    insert_facility(c, Facility(
        facility_id="SC-1", name="Sub-Centre One", level=FacilityLevel.SUB_CENTRE,
        village_or_area="Badimela", district="Nabarangpur", latitude=19.17, longitude=82.23,
    ))
    insert_facility(c, Facility(
        facility_id="PHC-1", name="PHC One", level=FacilityLevel.PHC,
        village_or_area="Umerkote", district="Nabarangpur",  # deliberately no coordinates
    ))
    yield c


def _patient(conn, **kw):
    args = dict(
        name="Sunita Majhi", age=27, gender="F", village="Badimela",
        home_facility_id="SC-1", registered_by="ASHA-TEST", risk_category=RiskCategory.MATERNAL,
    )
    args.update(kw)
    return register_patient(conn, **args)


def _by_type(bundle, resource_type):
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == resource_type]


def test_patient_only_bundle(conn) -> None:
    p = _patient(conn)

    bundle = build_patient_bundle(conn, p.patient_id)

    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "collection"
    patient = _by_type(bundle, "Patient")[0]
    assert patient["id"] == p.patient_id
    assert patient["gender"] == "female"
    assert patient["name"] == [{"text": "Sunita Majhi"}]
    assert "birthDate" not in patient  # age is stored, date of birth is not; never invent one
    assert patient["managingOrganization"] == {"reference": "Organization/SC-1"}
    assert _by_type(bundle, "ServiceRequest") == []
    assert _by_type(bundle, "Encounter") == []


def test_location_only_for_facilities_with_coordinates(conn) -> None:
    p = _patient(conn)
    create_referral(
        conn, patient_id=p.patient_id, from_facility_id="SC-1", to_facility_id="PHC-1",
        reason="ANC screening", urgency=UrgencyBand.SOON, created_by="ASHA-TEST",
    )

    bundle = build_patient_bundle(conn, p.patient_id)

    assert {o["id"] for o in _by_type(bundle, "Organization")} == {"SC-1", "PHC-1"}
    locations = _by_type(bundle, "Location")
    assert [loc["id"] for loc in locations] == ["SC-1"]
    assert locations[0]["position"] == {"latitude": 19.17, "longitude": 82.23}


def test_referral_maps_to_service_request(conn) -> None:
    p = _patient(conn)
    ref = create_referral(
        conn, patient_id=p.patient_id, from_facility_id="SC-1", to_facility_id="PHC-1",
        reason="Chest pain", urgency=UrgencyBand.EMERGENCY, created_by="ASHA-TEST",
    )
    update_referral_status(conn, ref.referral_id, ReferralStatus.ACCEPTED, "PHC-NURSE")
    update_referral_status(conn, ref.referral_id, ReferralStatus.IN_TRANSIT, "PHC-NURSE")

    sr = _by_type(build_patient_bundle(conn, p.patient_id), "ServiceRequest")[0]

    assert sr["id"] == ref.referral_id
    assert sr["status"] == "active"
    assert sr["priority"] == "stat"
    assert sr["intent"] == "order"
    assert sr["subject"] == {"reference": f"Patient/{p.patient_id}"}
    assert sr["performer"] == [{"reference": "Organization/PHC-1"}]
    assert sr["reasonCode"] == [{"text": "Chest pain"}]
    # FHIR has no IN_TRANSIT, so the original state must survive in the extension.
    assert {"url": "urn:medtrust:ext:referral-status", "valueCode": "IN_TRANSIT"} in sr["extension"]


@pytest.mark.parametrize(
    "final, fhir_status",
    [(ReferralStatus.COMPLETED, "completed"), (ReferralStatus.CANCELLED, "revoked")],
)
def test_terminal_referral_statuses(conn, final, fhir_status) -> None:
    p = _patient(conn)
    ref = create_referral(
        conn, patient_id=p.patient_id, from_facility_id="SC-1", to_facility_id="PHC-1",
        reason="r", urgency=UrgencyBand.ROUTINE, created_by="ASHA-TEST",
    )
    if final == ReferralStatus.COMPLETED:
        update_referral_status(conn, ref.referral_id, ReferralStatus.ACCEPTED, "x")
        update_referral_status(conn, ref.referral_id, ReferralStatus.IN_TRANSIT, "x")
    update_referral_status(conn, ref.referral_id, final, "x")

    sr = _by_type(build_patient_bundle(conn, p.patient_id), "ServiceRequest")[0]
    assert sr["status"] == fhir_status
    assert sr["priority"] == "routine"


def test_teleconsult_maps_to_virtual_encounter(conn) -> None:
    p = _patient(conn)
    ref = create_referral(
        conn, patient_id=p.patient_id, from_facility_id="SC-1", to_facility_id="PHC-1",
        reason="r", urgency=UrgencyBand.URGENT, created_by="ASHA-TEST",
    )
    s = schedule_session(conn, patient_id=p.patient_id, facility_id="PHC-1", doctor_name="Dr. Rao", referral_id=ref.referral_id)

    planned = _by_type(build_patient_bundle(conn, p.patient_id), "Encounter")[0]
    assert planned["status"] == "planned"
    assert "period" not in planned

    start_session(conn, s.session_id)
    complete_session(conn, s.session_id, "confidential consult notes")
    done = _by_type(build_patient_bundle(conn, p.patient_id), "Encounter")[0]

    assert done["status"] == "finished"
    assert done["class"]["code"] == "VR"
    assert done["participant"] == [{"individual": {"display": "Dr. Rao"}}]
    assert done["serviceProvider"] == {"reference": "Organization/PHC-1"}
    assert done["basedOn"] == [{"reference": f"ServiceRequest/{ref.referral_id}"}]
    assert {"start", "end"} <= set(done["period"])
    assert "confidential consult notes" not in str(done)  # notes are deliberately not exported


def test_unknown_facility_reference_does_not_break_export(conn) -> None:
    p = _patient(conn, home_facility_id="GONE-99")

    bundle = build_patient_bundle(conn, p.patient_id)

    patient = _by_type(bundle, "Patient")[0]
    assert "managingOrganization" not in patient


def test_unknown_gender_and_optional_phone(conn) -> None:
    p = _patient(conn, gender="X", phone="9876500000")

    patient = _by_type(build_patient_bundle(conn, p.patient_id), "Patient")[0]

    assert patient["gender"] == "unknown"
    assert patient["telecom"] == [{"system": "phone", "value": "9876500000"}]


def test_unknown_patient_raises(conn) -> None:
    with pytest.raises(PatientNotFoundError):
        build_patient_bundle(conn, "PAT-NOPE")


def test_every_entry_full_url_matches_its_resource(conn) -> None:
    p = _patient(conn)
    bundle = build_patient_bundle(conn, p.patient_id)
    for e in bundle["entry"]:
        assert e["fullUrl"].endswith(f"/{e['resource']['resourceType']}/{e['resource']['id']}")


def test_bundle_validates_against_fhir_r4b_models(conn) -> None:
    bundle_model = pytest.importorskip("fhir.resources.R4B.bundle")
    p = _patient(conn, phone="9876500000")
    ref = create_referral(
        conn, patient_id=p.patient_id, from_facility_id="SC-1", to_facility_id="PHC-1",
        reason="ANC screening", urgency=UrgencyBand.SOON, created_by="ASHA-TEST",
    )
    s = schedule_session(conn, patient_id=p.patient_id, facility_id="PHC-1", doctor_name="Dr. Rao", referral_id=ref.referral_id)
    start_session(conn, s.session_id)
    complete_session(conn, s.session_id, "notes")

    bundle = build_patient_bundle(conn, p.patient_id)
    parsed = bundle_model.Bundle.model_validate(bundle)

    kinds = sorted(e.resource.__class__.__name__ for e in parsed.entry)
    assert kinds == ["Encounter", "Location", "Organization", "Organization", "Patient", "ServiceRequest"]


def test_endpoint(conn) -> None:
    p = _patient(conn)
    app = FastAPI()
    app.include_router(fhir_router.router)
    app.dependency_overrides[fhir_router._db_dependency] = lambda: conn
    client = TestClient(app)

    ok = client.get(f"/fhir/patients/{p.patient_id}/bundle")
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("application/fhir+json")
    assert ok.json()["resourceType"] == "Bundle"

    assert client.get("/fhir/patients/PAT-NOPE/bundle").status_code == 404
