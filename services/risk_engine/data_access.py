from __future__ import annotations

from typing import Optional

from shared.schemas import Batch, BatchStatus, Supplier

try:
    from shared.database import get_connection  # noqa: F401
except ImportError:
    get_connection = None


def fetch_batch(batch_id: str) -> Optional[Batch]:
    from services.intake.storage import get_batch as _get_batch
    conn = get_connection()
    return _get_batch(conn, batch_id)


def fetch_supplier(supplier_id: str) -> Optional[Supplier]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM suppliers WHERE supplier_id = ?", (supplier_id,)
        ).fetchone()
    except Exception:
        return None  # suppliers table doesn't exist yet — expected for now

    if row is None:
        return None

    keys = row.keys()
    return Supplier(
        supplier_id=row["supplier_id"],
        name=row["name"] if "name" in keys else "",
        reject_rate_3mo=row["reject_rate_3mo"] if "reject_rate_3mo" in keys else 0.0,
        reject_rate_6mo=row["reject_rate_6mo"] if "reject_rate_6mo" in keys else 0.0,
        total_batches_supplied=row["total_batches_supplied"] if "total_batches_supplied" in keys else 0,
        flagged_incidents=[],
    )


def persist_batch_status(batch_id: str, status: BatchStatus) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE intake_batches SET status = ? WHERE batch_id = ?",
        (status.value, batch_id),
    )
    conn.commit()