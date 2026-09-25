"""
tests/test_diagnostics.py

Diagnostic order lifecycle (ORDERED -> SAMPLE_COLLECTED -> RESULT_AVAILABLE
-> REVIEWED, CANCELLED from any open state), capability-based auto-routing
to a diagnostics-capable facility, ledger/timeline integration, the
dashboard widget, and the HTTP endpoints.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.dashboard import router as dashboard_router
from services.diagnostics import router as diagnostics_router
from services.diagnostics.service import (
    DiagnosticOrderNotFoundError,
    InvalidDiagnosticTransitionError,
    NoDiagnosticsCapableFacilityError,
    cancel_order,
    create_diagnostic_order,
    mark_sample_collected,
    record_result,
    review_result,
)
from services.facilities.service import insert_facility
from services.patients.service import PatientNotFoundError, register_patient
from shared.schemas import DiagnosticResultFlag, DiagnosticStatus, Facility, FacilityLevel, RiskCategory


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    """A real init_db()-backed database: the dashboard widget joins across
    several tables, so the lightweight bare-schema fixture other tests use
    isn't enough here."""
    import shared.database as database_module

    monkeypatch.setattr(database_module, "DB_PATH", tmp_path / "diagnostics_test.db")
    monkeypatch.setattr(database_module, "_connection", None)
    database_module.init_db()
    c = database_module.get_connection()

    insert_facility(c, Facility(
        facility_id="SC-1", name="Sub-Centre One", level=FacilityLevel.SUB_CENTRE,
        village_or_area="Badimela", district="Nabarangpur", has_diagnostics=False,
        latitude=19.17, longitude=82.23,
    ))
    insert_facility(c, Facility(
        facility_id="PHC-1", name="PHC One", level=FacilityLevel.PHC,
        village_or_area="Umerkote", district="Nabarangpur", has_diagnostics=True,
        latitude=19.20, longitude=82.20,
    ))
    insert_facility(c, Facility(
        facility_id="PHC-2", name="PHC Two", level=FacilityLevel.PHC,
        village_or_area="Raighar", district="Nabarangpur", has_diagnostics=True,
        latitude=19.45, longitude=82.25,  # farther from SC-1 than PHC-1
    ))
    insert_facility(c, Facility(
        facility_id="CHC-NODIAG", name="CHC No Lab", level=FacilityLevel.CHC,
        village_or_area="Elsewhere", district="Nabarangpur", has_diagnostics=False,
        # deliberately no coordinates
    ))
    # Its own district, with no diagnostics-capable facility in it and no
    # coordinates -- neither geo-distance nor the same-district fallback in
    # find_nearest_facility can find anywhere to route to.
    insert_facility(c, Facility(
        facility_id="SC-ISOLATED", name="Sub-Centre Isolated", level=FacilityLevel.SUB_CENTRE,
        village_or_area="Faraway", district="Malkangiri", has_diagnostics=False,
    ))
    yield c


def _patient(conn, **kw):
    args = dict(
        name="Sunita Majhi", age=27, gender="F", village="Badimela",
        home_facility_id="SC-1", registered_by="ASHA-TEST", risk_category=RiskCategory.MATERNAL,
    )
    args.update(kw)
    return register_patient(conn, **args)


# ---------------------------------------------------------------- routing --

def test_order_stays_at_ordering_facility_when_it_has_diagnostics(conn) -> None:
    p = _patient(conn)
    order = create_diagnostic_order(
        conn, patient_id=p.patient_id, ordering_facility_id="PHC-1",
        test_type="Hemoglobin", reason="Anaemia screening", ordered_by="NURSE-1",
    )
    assert order.performing_facility_id == "PHC-1"
    assert order.routed is False


def test_order_auto_routes_to_nearest_diagnostics_capable_facility(conn) -> None:
    p = _patient(conn)
    order = create_diagnostic_order(
        conn, patient_id=p.patient_id, ordering_facility_id="SC-1",
        test_type="Malaria RDT", reason="Fever", ordered_by="ASHA-1",
    )
    # PHC-1 has diagnostics and is nearer to SC-1 than PHC-2.
    assert order.performing_facility_id == "PHC-1"
    assert order.routed is True


def test_order_raises_when_no_diagnostics_capable_facility_exists(conn) -> None:
    p = _patient(conn, home_facility_id="SC-ISOLATED")
    with pytest.raises(NoDiagnosticsCapableFacilityError):
        create_diagnostic_order(
            conn, patient_id=p.patient_id, ordering_facility_id="SC-ISOLATED",
            test_type="X-Ray", reason="Suspected fracture", ordered_by="NURSE-2",
        )


def test_create_order_unknown_patient_raises(conn) -> None:
    with pytest.raises(PatientNotFoundError):
        create_diagnostic_order(
            conn, patient_id="PAT-NOPE", ordering_facility_id="PHC-1",
            test_type="Hemoglobin", reason="r", ordered_by="NURSE-1",
        )


# ---------------------------------------------------------------- lifecycle --

def test_full_lifecycle_ordered_to_reviewed(conn) -> None:
    p = _patient(conn)
    order = create_diagnostic_order(
        conn, patient_id=p.patient_id, ordering_facility_id="PHC-1",
        test_type="Blood Sugar", reason="Diabetes follow-up", ordered_by="NURSE-1",
    )
    assert order.status == DiagnosticStatus.ORDERED

    order = mark_sample_collected(conn, order.diagnostic_id, "LAB-TECH-1")
    assert order.status == DiagnosticStatus.SAMPLE_COLLECTED
    assert order.sample_collected_at is not None

    order = record_result(
        conn, order.diagnostic_id, result_flag=DiagnosticResultFlag.ABNORMAL,
        result_summary="Fasting glucose 165 mg/dL", actor="LAB-TECH-1",
    )
    assert order.status == DiagnosticStatus.RESULT_AVAILABLE
    assert order.result_flag == DiagnosticResultFlag.ABNORMAL
    assert order.result_available_at is not None

    order = review_result(conn, order.diagnostic_id, "DR-RAO")
    assert order.status == DiagnosticStatus.REVIEWED
    assert order.reviewed_by == "DR-RAO"
    assert order.reviewed_at is not None


@pytest.mark.parametrize("from_status_step", ["ordered", "sample_collected", "result_available"])
def test_cancel_from_any_open_state(conn, from_status_step) -> None:
    p = _patient(conn)
    order = create_diagnostic_order(
        conn, patient_id=p.patient_id, ordering_facility_id="PHC-1",
        test_type="X-Ray", reason="r", ordered_by="NURSE-1",
    )
    if from_status_step in ("sample_collected", "result_available"):
        order = mark_sample_collected(conn, order.diagnostic_id, "LAB-TECH-1")
    if from_status_step == "result_available":
        order = record_result(
            conn, order.diagnostic_id, result_flag=DiagnosticResultFlag.NORMAL,
            result_summary="No fracture", actor="LAB-TECH-1",
        )

    order = cancel_order(conn, order.diagnostic_id, "NURSE-1", reason="Patient did not show")
    assert order.status == DiagnosticStatus.CANCELLED


def test_cannot_skip_from_ordered_to_result_available(conn) -> None:
    p = _patient(conn)
    order = create_diagnostic_order(
        conn, patient_id=p.patient_id, ordering_facility_id="PHC-1",
        test_type="Hemoglobin", reason="r", ordered_by="NURSE-1",
    )
    with pytest.raises(InvalidDiagnosticTransitionError):
        record_result(
            conn, order.diagnostic_id, result_flag=DiagnosticResultFlag.NORMAL,
            result_summary="x", actor="LAB-TECH-1",
        )


def test_terminal_states_reject_further_transitions(conn) -> None:
    p = _patient(conn)
    order = create_diagnostic_order(
        conn, patient_id=p.patient_id, ordering_facility_id="PHC-1",
        test_type="Hemoglobin", reason="r", ordered_by="NURSE-1",
    )
    order = cancel_order(conn, order.diagnostic_id, "NURSE-1")
    with pytest.raises(InvalidDiagnosticTransitionError):
        mark_sample_collected(conn, order.diagnostic_id, "LAB-TECH-1")


def test_unknown_order_raises(conn) -> None:
    with pytest.raises(DiagnosticOrderNotFoundError):
        mark_sample_collected(conn, "DX-NOPE", "LAB-TECH-1")


# ---------------------------------------------------------------- timeline --

def test_order_and_transitions_appear_on_patient_timeline(conn) -> None:
    from services.patients.service import get_patient_timeline

    p = _patient(conn)
    order = create_diagnostic_order(
        conn, patient_id=p.patient_id, ordering_facility_id="PHC-1",
        test_type="Hemoglobin", reason="r", ordered_by="NURSE-1",
    )
    mark_sample_collected(conn, order.diagnostic_id, "LAB-TECH-1")

    actions = [e["action"] for e in get_patient_timeline(conn, p.patient_id)]
    assert "DIAGNOSTIC_ORDERED" in actions
    assert "DIAGNOSTIC_SAMPLE_COLLECTED" in actions


# ---------------------------------------------------------------- dashboard --

def test_dashboard_diagnostics_widget(conn) -> None:
    p = _patient(conn)
    o1 = create_diagnostic_order(
        conn, patient_id=p.patient_id, ordering_facility_id="PHC-1",
        test_type="Hemoglobin", reason="r1", ordered_by="NURSE-1",
    )
    create_diagnostic_order(
        conn, patient_id=p.patient_id, ordering_facility_id="PHC-1",
        test_type="Blood Sugar", reason="r2", ordered_by="NURSE-1",
    )
    o3 = create_diagnostic_order(
        conn, patient_id=p.patient_id, ordering_facility_id="PHC-1",
        test_type="Malaria RDT", reason="r3", ordered_by="NURSE-1",
    )
    mark_sample_collected(conn, o3.diagnostic_id, "LAB-TECH-1")
    record_result(
        conn, o3.diagnostic_id, result_flag=DiagnosticResultFlag.CRITICAL,
        result_summary="Positive, high parasitaemia", actor="LAB-TECH-1",
    )
    mark_sample_collected(conn, o1.diagnostic_id, "LAB-TECH-1")
    record_result(
        conn, o1.diagnostic_id, result_flag=DiagnosticResultFlag.NORMAL,
        result_summary="Within range", actor="LAB-TECH-1",
    )
    review_result(conn, o1.diagnostic_id, "DR-RAO")  # reviewed — should not count anywhere

    app = FastAPI()
    app.include_router(dashboard_router.router)
    app.dependency_overrides[dashboard_router._db_dependency] = lambda: conn
    client = TestClient(app)

    data = client.get("/dashboard/PHC-1").json()["diagnostics"]
    assert data["pending"] == 1            # o2, still ORDERED
    assert data["awaiting_review"] == 1    # o3, RESULT_AVAILABLE
    assert data["critical_awaiting_review"] == 1  # o3


def test_dashboard_diagnostics_widget_zero_for_facility_with_no_orders(conn) -> None:
    app = FastAPI()
    app.include_router(dashboard_router.router)
    app.dependency_overrides[dashboard_router._db_dependency] = lambda: conn
    client = TestClient(app)

    data = client.get("/dashboard/PHC-2").json()["diagnostics"]
    assert data == {"pending": 0, "awaiting_review": 0, "critical_awaiting_review": 0}


def test_dashboard_diagnostics_widget_scoped_to_performing_facility(conn) -> None:
    p = _patient(conn)
    # Ordered from SC-1 (no diagnostics), auto-routed to PHC-1 — the number
    # should show up under PHC-1, not SC-1.
    create_diagnostic_order(
        conn, patient_id=p.patient_id, ordering_facility_id="SC-1",
        test_type="Malaria RDT", reason="r", ordered_by="ASHA-1",
    )

    app = FastAPI()
    app.include_router(dashboard_router.router)
    app.dependency_overrides[dashboard_router._db_dependency] = lambda: conn
    client = TestClient(app)

    assert client.get("/dashboard/PHC-1").json()["diagnostics"]["pending"] == 1
    assert client.get("/dashboard/SC-1").json()["diagnostics"]["pending"] == 0


# ---------------------------------------------------------------- HTTP endpoints --

@pytest.fixture()
def client(conn):
    app = FastAPI()
    app.include_router(diagnostics_router.router)
    app.dependency_overrides[diagnostics_router._db_dependency] = lambda: conn
    return TestClient(app)


def test_endpoint_full_flow(conn, client) -> None:
    p = _patient(conn)

    created = client.post("/diagnostics", json={
        "patient_id": p.patient_id, "ordering_facility_id": "PHC-1",
        "test_type": "Hemoglobin", "reason": "Anaemia screening", "ordered_by": "NURSE-1",
    })
    assert created.status_code == 200
    diagnostic_id = created.json()["diagnostic_id"]
    assert created.json()["status"] == "ORDERED"

    collected = client.post(f"/diagnostics/{diagnostic_id}/sample-collected", json={"actor": "LAB-TECH-1"})
    assert collected.status_code == 200 and collected.json()["status"] == "SAMPLE_COLLECTED"

    result = client.post(f"/diagnostics/{diagnostic_id}/result", json={
        "result_flag": "ABNORMAL", "result_summary": "Low Hb", "actor": "LAB-TECH-1",
    })
    assert result.status_code == 200 and result.json()["result_flag"] == "ABNORMAL"

    reviewed = client.post(f"/diagnostics/{diagnostic_id}/review", json={"actor": "DR-RAO"})
    assert reviewed.status_code == 200 and reviewed.json()["status"] == "REVIEWED"

    fetched = client.get(f"/diagnostics/{diagnostic_id}")
    assert fetched.status_code == 200 and fetched.json()["reviewed_by"] == "DR-RAO"

    listed = client.get("/diagnostics", params={"patient_id": p.patient_id})
    assert listed.status_code == 200 and len(listed.json()) == 1


def test_endpoint_invalid_transition_is_409(conn, client) -> None:
    p = _patient(conn)
    created = client.post("/diagnostics", json={
        "patient_id": p.patient_id, "ordering_facility_id": "PHC-1",
        "test_type": "Hemoglobin", "reason": "r", "ordered_by": "NURSE-1",
    })
    diagnostic_id = created.json()["diagnostic_id"]

    resp = client.post(f"/diagnostics/{diagnostic_id}/result", json={
        "result_flag": "NORMAL", "result_summary": "x", "actor": "LAB-TECH-1",
    })
    assert resp.status_code == 409


def test_endpoint_unknown_patient_is_404(conn, client) -> None:
    resp = client.post("/diagnostics", json={
        "patient_id": "PAT-NOPE", "ordering_facility_id": "PHC-1",
        "test_type": "Hemoglobin", "reason": "r", "ordered_by": "NURSE-1",
    })
    assert resp.status_code == 404


def test_endpoint_unroutable_order_is_422(conn, client) -> None:
    p = _patient(conn, home_facility_id="SC-ISOLATED")
    resp = client.post("/diagnostics", json={
        "patient_id": p.patient_id, "ordering_facility_id": "SC-ISOLATED",
        "test_type": "X-Ray", "reason": "r", "ordered_by": "NURSE-2",
    })
    assert resp.status_code == 422


def test_endpoint_unknown_order_is_404(client) -> None:
    resp = client.post("/diagnostics/DX-NOPE/sample-collected", json={"actor": "LAB-TECH-1"})
    assert resp.status_code == 404
