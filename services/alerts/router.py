"""services/alerts/router.py — read-only alert history + audio playback for the UI."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from shared.database import get_db
from services.alerts.service import get_alerts

router = APIRouter(prefix="/alerts", tags=["alerts"])

# Pre-generated spoken alerts live here (see data/generate_alert_audio.py).
# Filenames are alert_{lang}_{status}.mp3, all lowercase, matching exactly
# what that script writes -- so this endpoint never has to guess a naming
# convention, it just checks whether the exact file exists.
_AUDIO_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "audio"


def _db_dependency():
    with get_db() as conn:
        yield conn


@router.get("/{batch_id}")
def list_alerts(
    batch_id: str,
    lang: str | None = Query(default=None, max_length=8),
    db: sqlite3.Connection = Depends(_db_dependency),
):
    return {"batch_id": batch_id, "alerts": get_alerts(db, batch_id, lang)}


@router.get("/audio/{lang}/{status}")
def get_alert_audio(lang: str, status: str):
    """
    Serves a pre-generated spoken alert clip, e.g. GET /alerts/audio/hi/hold.
    Returns 404 (not a crash) when that language/status hasn't been
    recorded yet, so the frontend can hide the play button gracefully
    rather than show a broken player.
    """
    filename = f"alert_{lang.lower()}_{status.lower()}.mp3"
    file_path = _AUDIO_DIR / filename
    if not file_path.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"No audio recorded for {lang}/{status}")
    return FileResponse(file_path, media_type="audio/mpeg")


@router.get("/audio/{lang}/{status}/exists")
def check_alert_audio_exists(lang: str, status: str):
    """Cheap existence check so the frontend can decide whether to render
    the play button at all, without triggering a 404 in the network tab
    for every unavailable combination."""
    filename = f"alert_{lang.lower()}_{status.lower()}.mp3"
    return {"exists": (_AUDIO_DIR / filename).is_file()}