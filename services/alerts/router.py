"""services/alerts/router.py — read-only alert history for the UI."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from shared.database import get_db
from services.alerts.service import get_alerts

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _db_dependency():
    with get_db() as conn:
        yield conn


@router.get("/{batch_id}")
def list_alerts(batch_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    return {"batch_id": batch_id, "alerts": get_alerts(db, batch_id)}