"""
services/alerts/service.py

Outbound alerting for decisions and recalls.

Why this exists: a REJECT previously updated a screen. In a PHC nobody is
watching the screen. This module is the single call site for "tell a human
something happened", so the transport can be swapped (log table today, SMS
gateway tomorrow) without touching the risk router or recall.

Localisation reuses data/multilingual_alerts.py's TRANSLATIONS table rather
than defining a second copy of the same Hindi/Odia strings -- that module
owns the wording, this module owns the delivery.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

# Delivery channels. SMS is stubbed deliberately: the call site is real, the
# transport is not, and the demo says "queued for SMS" rather than claiming
# a message was sent.
CHANNEL_LOG = "LOG"
CHANNEL_SMS = "SMS"

_FALLBACK_TEMPLATES = {
    "EN": {
        "HOLD": "ALERT: Batch {batch_id} has been placed on HOLD pending review.",
        "REJECT": "CRITICAL: Batch {batch_id} has been REJECTED and must not be used.",
        "RECALL": "RECALL: Batch {batch_id} is being recalled. Quarantine all remaining stock immediately.",
    }
}


def ensure_alerts_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            alert_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            recipient TEXT NOT NULL,
            channel TEXT NOT NULL,
            language TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_batch ON alerts(batch_id)")
    conn.commit()


def _resolve_message(alert_type: str, language: str, batch_id: str) -> str:
    """
    Look the wording up in shared/alert_texts.py (pure data, 19 languages).
    Anything not covered there falls back to English, so an alert with
    plain-English text still reaches a human, which a crash does not.
    """
    lang = (language or "EN").lower()
    try:
        from shared.alert_texts import ALERT_TEXTS

        text = ALERT_TEXTS.get(lang, {}).get(alert_type)
        if text:
            return f"{text} [{batch_id}]"
    except Exception:  # noqa: BLE001 - localisation is best-effort
        pass

    template = _FALLBACK_TEMPLATES["EN"].get(
        alert_type, "ALERT regarding batch {batch_id}."
    )
    return template.format(batch_id=batch_id)


def send_alert(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    recipient: str,
    alert_type: str,
    channel: str = CHANNEL_LOG,
    language: str = "EN",
) -> dict:
    """
    Records one outbound alert. Returns the stored row as a dict.

    `status` is QUEUED for SMS (no gateway wired) and SENT for LOG, so the
    UI can show honestly what actually left the building.
    """
    message = _resolve_message(alert_type, language, batch_id)
    alert_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    status = "SENT" if channel == CHANNEL_LOG else "QUEUED"

    conn.execute(
        """
        INSERT INTO alerts (
            alert_id, batch_id, recipient, channel, language,
            alert_type, message, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert_id, batch_id, recipient, channel, language.upper(),
            alert_type, message, status, created_at.isoformat(),
        ),
    )
    conn.commit()

    return {
        "alert_id": alert_id,
        "batch_id": batch_id,
        "recipient": recipient,
        "channel": channel,
        "language": language.upper(),
        "alert_type": alert_type,
        "message": message,
        "status": status,
        "created_at": created_at.isoformat(),
    }


def broadcast_recall(
    conn: sqlite3.Connection, *, batch_id: str, recipients: list[str], language: str = "EN"
) -> list[dict]:
    """Fan a recall out over every resolved recipient from Step 7."""
    return [
        send_alert(
            conn,
            batch_id=batch_id,
            recipient=r,
            alert_type="RECALL",
            channel=CHANNEL_SMS,
            language=language,
        )
        for r in recipients
    ]


def get_alerts(conn: sqlite3.Connection, batch_id: str, lang: str | None = None) -> list[dict]:
    """Newest first. If `lang` is given, each message is re-rendered in that
    language (from the stored alert_type), so the UI can switch language
    without re-evaluating the batch."""
    rows = conn.execute(
        "SELECT * FROM alerts WHERE batch_id = ? ORDER BY created_at DESC",
        (batch_id,),
    ).fetchall()
    out = [dict(r) for r in rows]
    if lang:
        for a in out:
            a["message"] = _resolve_message(a["alert_type"], lang, batch_id)
            a["language"] = lang.upper()
    return out