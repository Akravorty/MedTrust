"""
data/seed_recall_demo.py

Seeds the DEMO-RECALL batch and its deterministic 4-stop distribution
trail (Central Store -> Pharmacy Store B -> Ward 3 -> Ward 7), so
POST /ledger/recall/simulate/DEMO-RECALL has real distribution history
to trace instead of raising RecallTraceIncompleteError.

Idempotent via idempotency_key on each distribution event and a
get_batch check before insert, same pattern as golden_batches.py.
"""
from __future__ import annotations

from datetime import date, datetime

from services.intake import storage
from services.ledger.service import log_event
from shared.schemas import Batch, BatchStatus

DISTRIBUTION_PATH = ["Central Store", "Pharmacy Store B", "Ward 3", "Ward 7"]


def generate_recall_demo(conn) -> None:
    print("Seeding DEMO-RECALL batch and distribution trail...")
    storage.ensure_schema(conn)

    if storage.get_batch(conn, "DEMO-RECALL") is None:
        demo_recall = Batch(
            batch_id="DEMO-RECALL",
            medicine_name="Ceftriaxone",
            batch_number="CFX-004",
            supplier_id="S-DEMO",
            ocr_qr_match_score=1.0,
            manufacture_date=date(2025, 6, 1),
            expiry_date=date(2027, 6, 1),
            received_timestamp=datetime.now(),
            status=BatchStatus.PENDING,
        )
        storage.insert_batch(
            conn, demo_recall,
            manual_review_reasons=None,
            image_fingerprint="demo-recall-fp",
        )
        print("Inserted DEMO-RECALL")
    else:
        print("DEMO-RECALL already exists, skipping")

    for i, location in enumerate(DISTRIBUTION_PATH):
        log_event(
            conn,
            batch_id="DEMO-RECALL",
            actor="system-demo-seed",
            action="DISTRIBUTION_EVENT",
            payload={"location": location, "sequence": i},
            idempotency_key=f"demo-recall-dist-{i}",
        )

    print("Distribution history seeded for DEMO-RECALL.")


if __name__ == "__main__":
    from shared.database import get_connection, init_db
    init_db()
    generate_recall_demo(get_connection())