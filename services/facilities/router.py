"""services/facilities/router.py — read-only facility directory + dashboard feed."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from shared.database import get_db
from services.facilities.service import find_nearest_facility, get_facility, list_facilities

router = APIRouter(prefix="/facilities", tags=["facilities"])


def _db_dependency():
    with get_db() as conn:
        yield conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "facility_id": row["facility_id"],
        "name": row["name"],
        "level": row["level"],
        "village_or_area": row["village_or_area"],
        "district": row["district"],
        "staff_count": row["staff_count"],
        "beds_total": row["beds_total"],
        "beds_occupied": row["beds_occupied"],
        "has_teleconsult": bool(row["has_teleconsult"]),
        "has_diagnostics": bool(row["has_diagnostics"]),
        "latitude": row["latitude"],
        "longitude": row["longitude"],
    }


@router.get("")
def get_facilities(district: str | None = None, db: sqlite3.Connection = Depends(_db_dependency)):
    rows = list_facilities(db, district)
    return [_row_to_dict(r) for r in rows]


@router.get("/{facility_id}")
def get_one_facility(facility_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    row = get_facility(db, facility_id)
    if row is None:
        return {"error": "Facility not found"}
    return _row_to_dict(row)


@router.get("/{facility_id}/nearest")
def get_nearest(
    facility_id: str, level: str | None = None, require_diagnostics: bool = False,
    db: sqlite3.Connection = Depends(_db_dependency),
):
    """Nearest facility to this one. `level` (e.g. PHC, CHC) narrows to that
    facility level; omit it to search every level. `require_diagnostics=true`
    restricts to facilities that can run diagnostics. `distance_km` is
    straight-line, and `distance_basis` says whether it was measured
    ("geo-distance") or a same-district match because coordinates are missing."""
    if get_facility(db, facility_id) is None:
        raise HTTPException(status_code=404, detail="Facility not found")
    nearest = find_nearest_facility(db, facility_id, level, require_diagnostics=require_diagnostics)
    if nearest is None:
        target = level or "any-level"
        raise HTTPException(status_code=404, detail=f"No {target} facility found near {facility_id}")
    return {
        **_row_to_dict(nearest.facility),
        "distance_km": nearest.distance_km,
        "distance_basis": nearest.basis,
    }
