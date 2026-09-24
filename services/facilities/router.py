"""services/facilities/router.py — read-only facility directory + dashboard feed."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from shared.database import get_db
from services.facilities.service import list_facilities, get_facility

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
