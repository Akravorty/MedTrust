"""
services/facilities/service.py

Facility master data: the sub-centre -> PHC -> CHC -> rural hospital ->
district hospital hierarchy that referrals and queues point at. Kept
deliberately simple (one flat table) since the PS's facility hierarchy is
just a level tag + district, not a real org chart.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from shared.schemas import Facility, FacilityLevel


def ensure_facilities_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facilities (
            facility_id      TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            level            TEXT NOT NULL,
            village_or_area  TEXT NOT NULL,
            district         TEXT NOT NULL,
            staff_count      INTEGER NOT NULL DEFAULT 0,
            beds_total       INTEGER NOT NULL DEFAULT 0,
            beds_occupied    INTEGER NOT NULL DEFAULT 0,
            has_teleconsult  INTEGER NOT NULL DEFAULT 1,
            has_diagnostics  INTEGER NOT NULL DEFAULT 1,
            latitude         REAL,
            longitude        REAL
        )
        """
    )
    # Databases created before geo-distance referrals / diagnostic routing
    # have no coordinate or has_diagnostics columns; add them in place so an
    # existing dev/demo DB keeps working.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(facilities)").fetchall()}
    for col in ("latitude", "longitude"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE facilities ADD COLUMN {col} REAL")
    if "has_diagnostics" not in existing_cols:
        conn.execute("ALTER TABLE facilities ADD COLUMN has_diagnostics INTEGER NOT NULL DEFAULT 1")


def insert_facility(conn: sqlite3.Connection, f: Facility) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO facilities
            (facility_id, name, level, village_or_area, district,
             staff_count, beds_total, beds_occupied, has_teleconsult, has_diagnostics, latitude, longitude)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f.facility_id, f.name, f.level.value, f.village_or_area, f.district,
            f.staff_count, f.beds_total, f.beds_occupied, int(f.has_teleconsult), int(f.has_diagnostics),
            f.latitude, f.longitude,
        ),
    )
    conn.commit()


def set_facility_coordinates(conn: sqlite3.Connection, facility_id: str, latitude: float, longitude: float) -> None:
    """Fill in coordinates for a facility that has none yet. Never overwrites an existing value."""
    conn.execute(
        "UPDATE facilities SET latitude = ?, longitude = ? WHERE facility_id = ? AND latitude IS NULL AND longitude IS NULL",
        (latitude, longitude, facility_id),
    )
    conn.commit()


def get_facility(conn: sqlite3.Connection, facility_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM facilities WHERE facility_id = ?", (facility_id,)
    ).fetchone()


def list_facilities(conn: sqlite3.Connection, district: str | None = None) -> list[sqlite3.Row]:
    if district:
        return conn.execute(
            "SELECT * FROM facilities WHERE district = ? ORDER BY level, name", (district,)
        ).fetchall()
    return conn.execute("SELECT * FROM facilities ORDER BY district, level, name").fetchall()


# Referral hierarchy: what level a facility should refer UP to. Used by the
# triage agent and the referral service to suggest the next facility.
_LEVEL_ORDER = [
    FacilityLevel.SUB_CENTRE,
    FacilityLevel.PHC,
    FacilityLevel.CHC,
    FacilityLevel.RURAL_HOSPITAL,
    FacilityLevel.DISTRICT_HOSPITAL,
]


def next_level_up(level: str) -> str | None:
    try:
        idx = _LEVEL_ORDER.index(FacilityLevel(level))
    except ValueError:
        return None
    if idx + 1 >= len(_LEVEL_ORDER):
        return None
    return _LEVEL_ORDER[idx + 1].value


def find_nearest_at_level(
    conn: sqlite3.Connection, district: str, level: str
) -> sqlite3.Row | None:
    """District-scoped fallback: first facility at the requested level in the
    district. Used when coordinates are missing; see find_nearest_facility for
    the real geo-distance lookup."""
    return conn.execute(
        "SELECT * FROM facilities WHERE district = ? AND level = ? LIMIT 1",
        (district, level),
    ).fetchone()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two lat/lon points."""
    r = 6371.0088  # mean Earth radius, km
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class NearestFacility:
    facility: sqlite3.Row
    distance_km: float | None  # straight-line distance; None when it could not be computed
    basis: str                 # "geo-distance" or "same-district"


def find_nearest_facility(
    conn: sqlite3.Connection, from_facility_id: str, level: str | None = None,
    *, require_diagnostics: bool = False,
) -> NearestFacility | None:
    """Nearest facility to `from_facility_id`.

    `level` narrows candidates to that facility level (e.g. PHC, CHC); pass
    None to search every level, which is what diagnostic-order routing does
    -- a test can be run by any diagnostics-capable facility, not just the
    next level up. `require_diagnostics=True` additionally restricts
    candidates to facilities with has_diagnostics set, for routing a
    diagnostic order to somewhere that can actually perform it.

    Uses straight-line (haversine) distance when the home facility and at
    least one candidate have coordinates, and ranks across district borders.
    Falls back to same-district matching when coordinates are missing, and
    says which basis was used so callers never present a district match as a
    measured distance. Distance is straight-line, not road/travel time."""
    home = get_facility(conn, from_facility_id)
    if home is None:
        return None

    clauses, params = ["facility_id != ?"], [from_facility_id]
    if level is not None:
        clauses.append("level = ?"); params.append(level)
    if require_diagnostics:
        clauses.append("has_diagnostics = 1")

    candidates = conn.execute(
        f"SELECT * FROM facilities WHERE {' AND '.join(clauses)}", params,
    ).fetchall()

    if home["latitude"] is not None and home["longitude"] is not None:
        located = [c for c in candidates if c["latitude"] is not None and c["longitude"] is not None]
        if located:
            scored = [
                (haversine_km(home["latitude"], home["longitude"], c["latitude"], c["longitude"]), c["facility_id"], c)
                for c in located
            ]
            distance, _, best = min(scored, key=lambda t: (t[0], t[1]))
            return NearestFacility(best, round(distance, 1), "geo-distance")

    same_district = [c for c in candidates if c["district"] == home["district"]]
    if same_district:
        return NearestFacility(same_district[0], None, "same-district")
    return None
