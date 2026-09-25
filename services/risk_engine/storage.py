"""
services/risk_engine/storage.py

SQLite persistence for RiskDecision rows.

Follows the same shape as services/intake/storage.py: one ensure_*_schema
registered from shared/database.py::init_db(), JSON columns for the list
fields, and no knowledge of HTTP or of other services' tables.

Why this exists: the router previously held decisions in a module-level
dict, so every decision was lost on restart and GET /risk/decisions/{id}
404'd on batches decided minutes earlier. The audit trail had a hole
exactly where the decision belonged.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from shared.schemas import RiskDecision, ShapContributor


def ensure_risk_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_decisions (
            batch_id TEXT PRIMARY KEY,
            risk_score REAL NOT NULL,
            decision TEXT NOT NULL,
            triggered_rule TEXT,
            shap_contributors TEXT NOT NULL DEFAULT '[]',
            reasons TEXT NOT NULL DEFAULT '[]',
            decided_at TEXT NOT NULL,
            model_version TEXT NOT NULL
        )
        """
    )
    conn.commit()


def save_decision(conn: sqlite3.Connection, decision: RiskDecision) -> None:
    """Upsert. Re-evaluating a batch overwrites its decision row -- the
    immutable history of decisions lives in the ledger, not here. This table
    answers 'what is the current decision for this batch'."""
    conn.execute(
        """
        INSERT INTO risk_decisions (
            batch_id, risk_score, decision, triggered_rule,
            shap_contributors, reasons, decided_at, model_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(batch_id) DO UPDATE SET
            risk_score        = excluded.risk_score,
            decision          = excluded.decision,
            triggered_rule    = excluded.triggered_rule,
            shap_contributors = excluded.shap_contributors,
            reasons           = excluded.reasons,
            decided_at        = excluded.decided_at,
            model_version     = excluded.model_version
        """,
        (
            decision.batch_id,
            float(decision.risk_score),
            decision.decision,
            decision.triggered_rule,
            json.dumps([c.model_dump(mode="json") for c in decision.shap_contributors]),
            json.dumps(list(decision.reasons)),
            decision.decided_at.isoformat(),
            decision.model_version,
        ),
    )
    conn.commit()


def get_decision(conn: sqlite3.Connection, batch_id: str) -> RiskDecision | None:
    row = conn.execute(
        "SELECT * FROM risk_decisions WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    if row is None:
        return None

    return RiskDecision(
        batch_id=row["batch_id"],
        risk_score=row["risk_score"],
        decision=row["decision"],
        triggered_rule=row["triggered_rule"],
        shap_contributors=[
            ShapContributor(**c) for c in json.loads(row["shap_contributors"] or "[]")
        ],
        reasons=json.loads(row["reasons"] or "[]"),
        decided_at=datetime.fromisoformat(row["decided_at"]),
        model_version=row["model_version"],
    )


# --------------------------------------------------------------- receipts --
#
# Records who received a batch. Step 2's risk_decisions table records what
# the system decided; this table records who signed for it -- the ledger's
# actor field on a finalize event was previously always "intake_service",
# a component name, so recall could never answer "who received it" and a
# human override was indistinguishable from an automatic accept.


def ensure_receipts_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS receipts (
            receipt_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            facility_id TEXT NOT NULL,
            received_by TEXT NOT NULL,
            role TEXT NOT NULL,
            decision TEXT NOT NULL,
            override_reason TEXT,
            signed_at TEXT NOT NULL,
            FOREIGN KEY (batch_id) REFERENCES intake_batches(batch_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_receipts_batch ON receipts(batch_id)"
    )
    conn.commit()


def save_receipt(
    conn: sqlite3.Connection,
    *,
    receipt_id: str,
    batch_id: str,
    facility_id: str,
    received_by: str,
    role: str,
    decision: str,
    override_reason: str | None,
    signed_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO receipts (
            receipt_id, batch_id, facility_id, received_by, role,
            decision, override_reason, signed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            receipt_id,
            batch_id,
            facility_id,
            received_by,
            role,
            decision,
            override_reason,
            signed_at.isoformat(),
        ),
    )
    conn.commit()


def get_receipt(conn: sqlite3.Connection, batch_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM receipts WHERE batch_id = ? ORDER BY signed_at DESC LIMIT 1",
        (batch_id,),
    ).fetchone()