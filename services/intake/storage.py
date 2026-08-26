"""
services/intake/storage.py

SQLite persistence for Batch rows plus content-fingerprint dedup, scoped
entirely inside Intake's own tables. Never writes to the ledger table
directly (Section 17) - Intake only ever calls the ledger HTTP contract.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, date

from shared.schemas import Batch, BatchStatus, TempLogEntry


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_batches (
            batch_id TEXT PRIMARY KEY,
            medicine_name TEXT NOT NULL,
            batch_number TEXT NOT NULL,
            supplier_id TEXT NOT NULL,
            qr_payload TEXT,
            ocr_extracted_text TEXT,
            ocr_qr_match_score REAL,
            manufacture_date TEXT,
            expiry_date TEXT,
            received_timestamp TEXT NOT NULL,
            storage_temp_log TEXT NOT NULL DEFAULT '[]',
            physical_inspection_notes TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            manual_review_reasons TEXT,
            image_fingerprint TEXT,
            finalized INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_intake_fingerprint ON intake_batches(image_fingerprint)"
    )
    conn.commit()


def _row_to_batch(row: sqlite3.Row) -> Batch:
    temp_log_raw = json.loads(row["storage_temp_log"] or "[]")
    return Batch(
        batch_id=row["batch_id"],
        medicine_name=row["medicine_name"],
        batch_number=row["batch_number"],
        supplier_id=row["supplier_id"],
        qr_payload=row["qr_payload"],
        ocr_extracted_text=json.loads(row["ocr_extracted_text"]) if row["ocr_extracted_text"] else None,
        ocr_qr_match_score=row["ocr_qr_match_score"],
        manufacture_date=date.fromisoformat(row["manufacture_date"]) if row["manufacture_date"] else None,
        expiry_date=date.fromisoformat(row["expiry_date"]) if row["expiry_date"] else None,
        received_timestamp=datetime.fromisoformat(row["received_timestamp"]),
        storage_temp_log=[TempLogEntry(**e) for e in temp_log_raw],
        physical_inspection_notes=row["physical_inspection_notes"],
        status=BatchStatus(row["status"]),
    )


def insert_batch(conn: sqlite3.Connection, batch: Batch, *, manual_review_reasons: list[str] | None, image_fingerprint: str | None) -> None:
    conn.execute(
        """
        INSERT INTO intake_batches (
            batch_id, medicine_name, batch_number, supplier_id, qr_payload,
            ocr_extracted_text, ocr_qr_match_score, manufacture_date, expiry_date,
            received_timestamp, storage_temp_log, physical_inspection_notes,
            status, manual_review_reasons, image_fingerprint, finalized
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            batch.batch_id,
            batch.medicine_name,
            batch.batch_number,
            batch.supplier_id,
            batch.qr_payload,
            json.dumps(batch.ocr_extracted_text) if batch.ocr_extracted_text is not None else None,
            batch.ocr_qr_match_score,
            batch.manufacture_date.isoformat() if batch.manufacture_date else None,
            batch.expiry_date.isoformat() if batch.expiry_date else None,
            batch.received_timestamp.isoformat(),
            json.dumps([e.model_dump(mode="json") for e in batch.storage_temp_log]),
            batch.physical_inspection_notes,
            batch.status.value,
            json.dumps(manual_review_reasons or []),
            image_fingerprint,
        ),
    )
    conn.commit()


def get_batch(conn: sqlite3.Connection, batch_id: str) -> Batch | None:
    row = conn.execute("SELECT * FROM intake_batches WHERE batch_id = ?", (batch_id,)).fetchone()
    if row is None:
        return None
    return _row_to_batch(row)


def get_batch_row(conn: sqlite3.Connection, batch_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM intake_batches WHERE batch_id = ?", (batch_id,)).fetchone()


def find_batch_by_fingerprint(conn: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM intake_batches WHERE image_fingerprint = ? ORDER BY received_timestamp ASC LIMIT 1",
        (fingerprint,),
    ).fetchone()


def mark_finalized(conn: sqlite3.Connection, batch_id: str) -> None:
    conn.execute("UPDATE intake_batches SET finalized = 1 WHERE batch_id = ?", (batch_id,))
    conn.commit()


def get_manual_review_reasons(conn: sqlite3.Connection, batch_id: str) -> list[str]:
    row = conn.execute("SELECT manual_review_reasons FROM intake_batches WHERE batch_id = ?", (batch_id,)).fetchone()
    if row is None or not row["manual_review_reasons"]:
        return []
    return json.loads(row["manual_review_reasons"])