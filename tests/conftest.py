"""
Shared pytest fixtures for the Intake test suite.

Uses an isolated temp SQLite DB per test session (not the dev meditrust.db)
so running tests never clobbers real dev data, and each test function gets
a clean set of tables via truncation.
"""

import sys
import os
import tempfile
import sqlite3
import pytest
import numpy as np
import cv2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    """A fresh SQLite connection with intake + ledger schema, isolated per test."""
    import shared.database as database_module

    db_path = tmp_path / "test_meditrust.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(database_module, "_connection", None)

    conn = database_module.get_connection()
    from services.ledger.service import ensure_ledger_schema
    from services.intake.service import ensure_intake_schema

    ensure_ledger_schema(conn)
    ensure_intake_schema(conn)
    conn.commit()
    yield conn


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """A TestClient wired to an isolated DB, with lifespan events triggered."""
    import shared.database as database_module

    db_path = tmp_path / "test_meditrust_app.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(database_module, "_connection", None)

    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        yield client


def _render_qr(payload: str, target_size: int = 200):
    """Renders a QR at an integer-multiple scale of its native module grid,
    so resizing never breaks module alignment (which silently corrupts
    detection for longer payloads at a fixed-size resize)."""
    encoder = cv2.QRCodeEncoder_create()
    qr = encoder.encode(payload)
    native = qr.shape[0]
    scale = max(4, target_size // native)
    qr_scaled = cv2.resize(qr, (native * scale, native * scale), interpolation=cv2.INTER_NEAREST)
    # Add a white quiet-zone border - real QR codes need one, and without
    # it small/edge-of-frame codes are harder to detect reliably.
    border = scale * 4
    bordered = cv2.copyMakeBorder(qr_scaled, border, border, border, border, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(bordered, cv2.COLOR_GRAY2BGR)


def make_blank_image(value=255, size=(300, 300)):
    img = np.ones((size[0], size[1], 3), dtype=np.uint8) * value
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def make_label_image_with_qr(
    medicine_name="Paracetamol 500mg",
    batch_number="B12345",
    expiry="12/2027",
    supplier_id="SUP-1",
    qr_batch_number=None,
    include_qr=True,
    blur=False,
):
    """Synthesizes a deterministic medicine-label-like image with printed
    text and an embedded QR code encoding the same (or deliberately
    mismatched) fields, for reproducible tests without external fixtures."""
    img = np.ones((520, 650, 3), dtype=np.uint8) * 255
    cv2.putText(img, medicine_name, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    cv2.putText(img, f"BATCH: {batch_number}", (30, 120), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    cv2.putText(img, f"EXP: {expiry}", (30, 180), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)

    if include_qr:
        import json as _json

        payload = _json.dumps(
            {
                "batch_number": qr_batch_number if qr_batch_number is not None else batch_number,
                "medicine_name": medicine_name,
                "expiry_date": expiry,
                "supplier_id": supplier_id,
            }
        )
        qr_bgr = _render_qr(payload, target_size=180)
        h, w = qr_bgr.shape[:2]
        img[230:230 + h, 30:30 + w] = qr_bgr

    if blur:
        img = cv2.GaussianBlur(img, (25, 25), 15)

    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()