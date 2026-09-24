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

# Step 7: each stop now carries facility_id and a named recipient, so
# recall can answer "who signed for this, where" instead of listing places.
DISTRIBUTION_PATH = [
    {"location": "Central Store",    "facility_id": "PHC-CENTRAL", "recipient": "R. Mahato (Store Keeper)"},
    {"location": "Pharmacy Store B", "facility_id": "PHC-PHARM-B", "recipient": "S. Behera (Pharmacist)"},
    {"location": "Ward 3",           "facility_id": "PHC-WARD-03", "recipient": "Sr. A. Das (Ward Nurse)"},
    {"location": "Ward 7",           "facility_id": "PHC-WARD-07", "recipient": "Sr. M. Patra (Ward Nurse)"},
]


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

    for i, stop in enumerate(DISTRIBUTION_PATH):
        log_event(
            conn,
            batch_id="DEMO-RECALL",
            actor=stop["recipient"],
            action="DISTRIBUTION_EVENT",
            payload={
                "location": stop["location"],
                "facility_id": stop["facility_id"],
                "recipient": stop["recipient"],
                "sequence": i,
            },
            # Bumped to -v2: the -v1 keys already exist in any database
            # seeded before Step 7, and idempotency would otherwise skip
            # the richer payload entirely, leaving recall with old rows.
            idempotency_key=f"demo-recall-dist-v2-{i}",
        )

    print("Distribution history seeded for DEMO-RECALL.")


if __name__ == "__main__":
    from shared.database import get_connection, init_db
    init_db()
    generate_recall_demo(get_connection())