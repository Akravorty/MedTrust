"""
services/facilities/service.py

Facility master data: the sub-centre -> PHC -> CHC -> rural hospital ->
district hospital hierarchy that referrals and queues point at. Kept
deliberately simple (one flat table) since the PS's facility hierarchy is
just a level tag + district, not a real org chart.
"""

from __future__ import annotations

import sqlite3

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
            has_teleconsult  INTEGER NOT NULL DEFAULT 1
        )
        """
    )


def insert_facility(conn: sqlite3.Connection, f: Facility) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO facilities
            (facility_id, name, level, village_or_area, district,
             staff_count, beds_total, beds_occupied, has_teleconsult)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f.facility_id, f.name, f.level.value, f.village_or_area, f.district,
            f.staff_count, f.beds_total, f.beds_occupied, int(f.has_teleconsult),
        ),
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
    """Simplified 'nearest' = same district, requested level, first match.
    A real system would use geo-distance; district-scoping is a defensible
    stand-in for a hackathon prototype and is documented as such."""
    return conn.execute(
        "SELECT * FROM facilities WHERE district = ? AND level = ? LIMIT 1",
        (district, level),
    ).fetchone()
