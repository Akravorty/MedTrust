# data/golden_batches.py
from datetime import date, datetime, timedelta

from shared.database import get_connection, init_db
from shared.schemas import Batch, TempLogEntry
from services.intake import storage

def generate_batches(conn) -> None:
    print("Generating synthetic medication batches...")
    storage.ensure_schema(conn)  # make sure intake_batches exists

    demo_accept = Batch(
        batch_id="DEMO-ACCEPT",
        medicine_name="Paracetamol",
        batch_number="BN-DEMO-1",
        supplier_id="S-DEMO",
        ocr_qr_match_score=1.0,
        manufacture_date=date.today() - timedelta(days=30),
        expiry_date=date.today() + timedelta(days=365),
        received_timestamp=datetime.now(),
        storage_temp_log=[TempLogEntry(timestamp=date.today(), temp_c=5.0)],
    )

    demo_reject = Batch(
        batch_id="DEMO-REJECT",
        medicine_name="Paracetamol",
        batch_number="BN-DEMO-2",
        supplier_id="S-DEMO",
        ocr_qr_match_score=0.9,
        manufacture_date=date.today() - timedelta(days=400),
        expiry_date=date.today() - timedelta(days=10),  # expired -> hard REJECT rule
        received_timestamp=datetime.now(),
    )

    demo_hold = Batch(
        batch_id="DEMO-HOLD",
        medicine_name="Amoxicillin",
        batch_number="BN-DEMO-3",
        supplier_id="S-DEMO",
        ocr_qr_match_score=0.75,   # moderate label/QR mismatch -> pushes the score into the HOLD band
        manufacture_date=date.today() - timedelta(days=100),
        expiry_date=date.today() + timedelta(days=200),
        received_timestamp=datetime.now(),
        storage_temp_log=[TempLogEntry(timestamp=date.today(), temp_c=9.0)],
    )

    for b in (demo_accept, demo_reject, demo_hold):
        if storage.get_batch(conn, b.batch_id) is None:
            storage.insert_batch(conn, b, manual_review_reasons=None, image_fingerprint=None)
            print(f"Inserted {b.batch_id}")
        else:
            print(f"{b.batch_id} already exists, skipping")

    print("Batches generated successfully.")

if __name__ == "__main__":
    init_db()
    conn = get_connection()
    generate_batches(conn)