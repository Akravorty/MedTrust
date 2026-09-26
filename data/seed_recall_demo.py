"""
data/seed_recall_demo.py

Seeds distribution trails for every demo batch (DEMO-ACCEPT, DEMO-HOLD,
DEMO-REJECT, DEMO-RECALL), so POST /ledger/recall/simulate/<batch_id> has
real distribution history to trace for any of them, instead of only
DEMO-RECALL working and the rest raising RecallTraceIncompleteError.

Each batch keeps its own distinct, realistic path -- never copy-pasted
identical stops -- since these are meant to look like independent real
shipments in a live demo, not four labels pointing at the same fake data.

Idempotent via idempotency_key on each distribution event and a
get_batch check before insert, same pattern as golden_batches.py. Only
seeds a distribution trail for a batch that already exists in
intake_batches -- this script never creates DEMO-ACCEPT/HOLD/REJECT
themselves (that's golden_batches.py's job), so a batch missing here
means the golden-batch seed hasn't run yet, not something to paper over.
"""
from __future__ import annotations

from datetime import date, datetime

from services.intake import storage
from services.ledger.service import log_event
from shared.schemas import Batch, BatchStatus

# Step 7: each stop carries facility_id and a named recipient, so recall
# can answer "who signed for this, where" instead of listing places.
DISTRIBUTION_PATHS: dict[str, list[dict]] = {
    "DEMO-RECALL": [
        {"location": "Central Store",    "facility_id": "PHC-CENTRAL",  "recipient": "R. Mahato (Store Keeper)"},
        {"location": "Pharmacy Store B", "facility_id": "PHC-PHARM-B",  "recipient": "S. Behera (Pharmacist)"},
        {"location": "Ward 3",           "facility_id": "PHC-WARD-03",  "recipient": "Sr. A. Das (Ward Nurse)"},
        {"location": "Ward 7",           "facility_id": "PHC-WARD-07",  "recipient": "Sr. M. Patra (Ward Nurse)"},
    ],
    "DEMO-HOLD": [
        {"location": "District Store",   "facility_id": "DH-NABAR",     "recipient": "P. Naik (Store Keeper)"},
        {"location": "CHC Pharmacy",     "facility_id": "CHC-NABAR",    "recipient": "T. Sahu (Pharmacist)"},
        {"location": "Ward 2",           "facility_id": "CHC-WARD-02",  "recipient": "Sr. K. Panda (Ward Nurse)"},
    ],
    "DEMO-REJECT": [
        {"location": "Intake Bay",       "facility_id": "PHC-UMER",     "recipient": "B. Majhi (QA Officer)"},
        {"location": "Quarantine Store", "facility_id": "PHC-UMER",     "recipient": "B. Majhi (QA Officer)"},
    ],
    "DEMO-ACCEPT": [
        {"location": "Central Store",    "facility_id": "PHC-RAIGH",    "recipient": "L. Toppo (Store Keeper)"},
        {"location": "Pharmacy Store A", "facility_id": "PHC-RAIGH",    "recipient": "M. Oram (Pharmacist)"},
        {"location": "Ward 1",           "facility_id": "PHC-WARD-01",  "recipient": "Sr. R. Nayak (Ward Nurse)"},
    ],
}


def generate_recall_demo(conn) -> None:
    print("Seeding distribution trails for demo batches...")
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

    for batch_id, path in DISTRIBUTION_PATHS.items():
        if storage.get_batch(conn, batch_id) is None:
            print(f"{batch_id} not found in intake_batches yet -- skipping "
                  f"its distribution trail (run golden_batches.py first).")
            continue

        for i, stop in enumerate(path):
            log_event(
                conn,
                batch_id=batch_id,
                actor=stop["recipient"],
                action="DISTRIBUTION_EVENT",
                payload={
                    "location": stop["location"],
                    "facility_id": stop["facility_id"],
                    "recipient": stop["recipient"],
                    "sequence": i,
                },
                # -v2: -v1 keys already exist in databases seeded before
                # Step 7; idempotency would otherwise skip the richer
                # payload entirely, leaving recall with old rows.
                idempotency_key=f"demo-recall-dist-v2-{batch_id}-{i}",
            )
        print(f"Distribution history seeded for {batch_id} ({len(path)} stops).")


if __name__ == "__main__":
    from shared.database import get_connection, init_db
    init_db()
    generate_recall_demo(get_connection())
