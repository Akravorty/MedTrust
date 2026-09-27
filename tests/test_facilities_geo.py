"""
tests/test_facilities_geo.py

Geo-distance "nearest facility" for referrals: haversine maths, ranking by real
distance (not insertion order), cross-district behaviour, the honest
same-district fallback when coordinates are missing, the in-place schema
migration for pre-existing databases, and the two places it is exposed
(triage agent tool + /facilities/{id}/nearest).
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.facilities import router as facilities_router
from services.facilities.service import (
    ensure_facilities_schema,
    find_nearest_facility,
    haversine_km,
    insert_facility,
    set_facility_coordinates,
)
from services.triage import tools as triage_tools
from shared.schemas import Facility, FacilityLevel


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    ensure_facilities_schema(c)
    yield c
    c.close()


def _fac(conn, fid, level, district="D1", lat=None, lon=None):
    insert_facility(conn, Facility(
        facility_id=fid, name=fid, level=level, village_or_area="v", district=district,
        latitude=lat, longitude=lon,
    ))


def test_haversine_one_degree_of_latitude_is_about_111_km() -> None:
    assert haversine_km(10.0, 80.0, 11.0, 80.0) == pytest.approx(111.2, abs=0.3)


def test_haversine_zero_for_same_point() -> None:
    assert haversine_km(19.2, 82.5, 19.2, 82.5) == 0.0


def test_nearest_is_by_distance_not_insertion_order(conn) -> None:
    _fac(conn, "SC-1", FacilityLevel.SUB_CENTRE, lat=19.36, lon=82.44)
    _fac(conn, "PHC-FAR", FacilityLevel.PHC, lat=19.20, lon=82.20)   # inserted first, but farther
    _fac(conn, "PHC-NEAR", FacilityLevel.PHC, lat=19.45, lon=82.25)

    result = find_nearest_facility(conn, "SC-1", "PHC")

    assert result is not None
    assert result.facility["facility_id"] == "PHC-NEAR"
    assert result.basis == "geo-distance"
    assert 0 < result.distance_km < haversine_km(19.36, 82.44, 19.20, 82.20)


def test_geo_distance_ranks_across_district_borders(conn) -> None:
    _fac(conn, "SC-1", FacilityLevel.SUB_CENTRE, district="D1", lat=10.00, lon=80.00)
    _fac(conn, "PHC-SAME-DISTRICT-FAR", FacilityLevel.PHC, district="D1", lat=10.50, lon=80.00)
    _fac(conn, "PHC-OTHER-DISTRICT-NEAR", FacilityLevel.PHC, district="D2", lat=10.02, lon=80.00)

    result = find_nearest_facility(conn, "SC-1", "PHC")

    assert result.facility["facility_id"] == "PHC-OTHER-DISTRICT-NEAR"
    assert result.distance_km == pytest.approx(2.2, abs=0.1)


def test_falls_back_to_same_district_when_home_has_no_coordinates(conn) -> None:
    _fac(conn, "SC-1", FacilityLevel.SUB_CENTRE, district="D1")  # no coords
    _fac(conn, "PHC-OTHER", FacilityLevel.PHC, district="D2", lat=10.0, lon=80.0)
    _fac(conn, "PHC-MINE", FacilityLevel.PHC, district="D1", lat=10.0, lon=80.0)

    result = find_nearest_facility(conn, "SC-1", "PHC")

    assert result.facility["facility_id"] == "PHC-MINE"
    assert result.basis == "same-district"
    assert result.distance_km is None  # never present a district match as a measured distance


def test_falls_back_to_same_district_when_no_candidate_has_coordinates(conn) -> None:
    _fac(conn, "SC-1", FacilityLevel.SUB_CENTRE, district="D1", lat=10.0, lon=80.0)
    _fac(conn, "PHC-1", FacilityLevel.PHC, district="D1")

    result = find_nearest_facility(conn, "SC-1", "PHC")

    assert result.facility["facility_id"] == "PHC-1"
    assert result.basis == "same-district"


def test_excludes_the_home_facility_itself(conn) -> None:
    _fac(conn, "PHC-A", FacilityLevel.PHC, lat=10.0, lon=80.0)
    _fac(conn, "PHC-B", FacilityLevel.PHC, lat=10.5, lon=80.0)

    assert find_nearest_facility(conn, "PHC-A", "PHC").facility["facility_id"] == "PHC-B"


def test_none_when_unknown_home_or_no_candidates(conn) -> None:
    _fac(conn, "SC-1", FacilityLevel.SUB_CENTRE, lat=10.0, lon=80.0)
    assert find_nearest_facility(conn, "NOPE", "PHC") is None
    assert find_nearest_facility(conn, "SC-1", "CHC") is None


def test_tie_break_is_deterministic(conn) -> None:
    _fac(conn, "SC-1", FacilityLevel.SUB_CENTRE, lat=10.0, lon=80.0)
    _fac(conn, "PHC-B", FacilityLevel.PHC, lat=10.1, lon=80.0)
    _fac(conn, "PHC-A", FacilityLevel.PHC, lat=9.9, lon=80.0)  # same distance either side

    assert find_nearest_facility(conn, "SC-1", "PHC").facility["facility_id"] == "PHC-A"


def test_existing_database_without_coordinate_columns_is_migrated_in_place() -> None:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE facilities (
            facility_id TEXT PRIMARY KEY, name TEXT NOT NULL, level TEXT NOT NULL,
            village_or_area TEXT NOT NULL, district TEXT NOT NULL,
            staff_count INTEGER NOT NULL DEFAULT 0, beds_total INTEGER NOT NULL DEFAULT 0,
            beds_occupied INTEGER NOT NULL DEFAULT 0, has_teleconsult INTEGER NOT NULL DEFAULT 1)"""
    )
    c.execute("INSERT INTO facilities (facility_id, name, level, village_or_area, district) VALUES ('OLD','Old','PHC','v','D1')")

    ensure_facilities_schema(c)
    ensure_facilities_schema(c)  # idempotent

    row = c.execute("SELECT * FROM facilities WHERE facility_id = 'OLD'").fetchone()
    assert row["name"] == "Old"
    assert row["latitude"] is None and row["longitude"] is None


def test_set_facility_coordinates_fills_gaps_but_never_overwrites(conn) -> None:
    _fac(conn, "PHC-1", FacilityLevel.PHC)
    set_facility_coordinates(conn, "PHC-1", 10.0, 80.0)
    set_facility_coordinates(conn, "PHC-1", 55.0, 55.0)

    row = conn.execute("SELECT latitude, longitude FROM facilities WHERE facility_id = 'PHC-1'").fetchone()
    assert (row["latitude"], row["longitude"]) == (10.0, 80.0)


def test_triage_referral_tool_reports_distance_and_basis(conn, monkeypatch: pytest.MonkeyPatch) -> None:
    _fac(conn, "SC-1", FacilityLevel.SUB_CENTRE, lat=19.36, lon=82.44)
    _fac(conn, "PHC-FAR", FacilityLevel.PHC, lat=19.20, lon=82.20)
    _fac(conn, "PHC-NEAR", FacilityLevel.PHC, lat=19.45, lon=82.25)
    monkeypatch.setattr(triage_tools, "get_connection", lambda: conn)

    out = triage_tools.find_referral_target({"from_facility_id": "SC-1", "target_level": "PHC"})

    assert out["facility_id"] == "PHC-NEAR"
    assert out["distance_basis"] == "geo-distance"
    assert isinstance(out["distance_km"], float)


def test_triage_referral_tool_errors_are_plain_dicts(conn, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(triage_tools, "get_connection", lambda: conn)
    assert "error" in triage_tools.find_referral_target({"from_facility_id": "NOPE", "target_level": "PHC"})


def test_nearest_endpoint(conn) -> None:
    _fac(conn, "SC-1", FacilityLevel.SUB_CENTRE, lat=19.36, lon=82.44)
    _fac(conn, "PHC-NEAR", FacilityLevel.PHC, lat=19.45, lon=82.25)
    app = FastAPI()
    app.include_router(facilities_router.router)
    app.dependency_overrides[facilities_router._db_dependency] = lambda: conn
    client = TestClient(app)

    ok = client.get("/facilities/SC-1/nearest", params={"level": "PHC"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["facility_id"] == "PHC-NEAR"
    assert body["distance_basis"] == "geo-distance"
    assert body["latitude"] == 19.45

    assert client.get("/facilities/NOPE/nearest", params={"level": "PHC"}).status_code == 404
    assert client.get("/facilities/SC-1/nearest", params={"level": "CHC"}).status_code == 404
