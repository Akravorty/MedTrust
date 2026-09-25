# data/seed_realistic_batches.py
"""
Realistic medicine batch seed data.

The original golden_batches.py seeds exactly 3 hardcoded IDs
(DEMO-ACCEPT/HOLD/REJECT). That's fine for unit tests but means every
other batch ID you might scan or type — including anything that looks
like it came off a real pack — returns "Batch not found". This script
adds ~80 batches with real Indian medicine names, realistic manufacturer
names, and a spread of expiry dates/match scores so ACCEPT/HOLD/REJECT
all occur naturally across the set, without needing every batch routed
through the live risk-scoring endpoint at every startup.

Note: since these are inserted with status pre-set (not run through
services/risk_engine live), the human-readable "reasons" that the real
pipeline attaches are not present on these rows. The dashboard's
medicine-availability widget only needs status counts, which this
provides; if you want SHAP explainability on a specific one of these
batches for the demo, feed it through /risk/evaluate once at runtime.
"""

import random
from datetime import date, datetime, timedelta

from shared.database import get_connection, init_db
from shared.schemas import Batch, BatchStatus, TempLogEntry
from services.intake import storage

_MEDICINES = [
    "Paracetamol 500mg", "Azithromycin 250mg", "Metformin 500mg", "Amoxicillin 250mg",
    "Amlodipine 5mg", "Atorvastatin 10mg", "Omeprazole 20mg", "Cefixime 200mg",
    "Ibuprofen 400mg", "Losartan 50mg", "Metronidazole 400mg", "Ciprofloxacin 500mg",
    "Salbutamol Inhaler", "ORS Powder", "Iron Folic Acid Tablets", "Vitamin D3 60K",
    "Insulin Glargine", "Doxycycline 100mg", "Diclofenac Gel", "Ranitidine 150mg",
    "Cough Syrup (Dextromethorphan)", "ORS + Zinc Combo", "Pantoprazole 40mg",
    "Levothyroxine 50mcg", "Clopidogrel 75mg",
]

_MANUFACTURERS = [
    "Sun Pharmaceutical Industries", "Cipla Ltd", "Dr. Reddy's Laboratories",
    "Lupin Ltd", "Zydus Lifesciences", "Aurobindo Pharma", "Mankind Pharma",
    "Torrent Pharmaceuticals", "Alkem Laboratories", "Glenmark Pharmaceuticals",
]

_DISTRICTS = ["Nabarangpur", "Koraput", "Kalahandi", "Bolangir", "Rayagada"]


def _random_batch_number(rng: random.Random) -> str:
    return f"BN{rng.randint(100000, 999999)}"


def generate_realistic_batches(conn) -> None:
    print("Generating realistic medicine batch seed data...")
    storage.ensure_schema(conn)

    rng = random.Random(42)  # fixed seed: same demo data every run, still "random"-looking
    inserted = 0

    for i in range(80):
        medicine = rng.choice(_MEDICINES)
        manufacturer = rng.choice(_MANUFACTURERS)
        district = rng.choice(_DISTRICTS)
        batch_id = f"MED-{district[:3].upper()}-{2000 + i}"

        if storage.get_batch(conn, batch_id) is not None:
            continue

        # Spread outcomes: ~65% clean ACCEPT, ~20% HOLD (borderline match or
        # near expiry), ~15% REJECT (expired or bad match) — roughly mirrors
        # the KPI dashboard screenshot's 91.5% pass rate at the top level.
        roll = rng.random()
        received_ts = datetime.now() - timedelta(hours=rng.randint(0, 36))

        if roll < 0.65:
            match_score = round(rng.uniform(0.92, 1.0), 2)
            mfg_days_ago = rng.randint(20, 300)
            expiry_days_ahead = rng.randint(120, 700)
            status = BatchStatus.ACCEPTED
        elif roll < 0.85:
            match_score = round(rng.uniform(0.6, 0.84), 2)
            mfg_days_ago = rng.randint(200, 500)
            expiry_days_ahead = rng.randint(15, 60)  # near-expiry -> HOLD-worthy
            status = BatchStatus.HOLD
        else:
            match_score = round(rng.uniform(0.3, 0.7), 2)
            mfg_days_ago = rng.randint(400, 800)
            expiry_days_ahead = rng.randint(-60, -1)  # already expired -> REJECT
            status = BatchStatus.REJECTED

        batch = Batch(
            batch_id=batch_id,
            medicine_name=medicine,
            batch_number=_random_batch_number(rng),
            supplier_id=f"SUP-{manufacturer.split()[0][:4].upper()}",
            ocr_qr_match_score=match_score,
            manufacture_date=date.today() - timedelta(days=mfg_days_ago),
            expiry_date=date.today() + timedelta(days=expiry_days_ahead),
            received_timestamp=received_ts,
            storage_temp_log=[TempLogEntry(timestamp=date.today(), temp_c=round(rng.uniform(2.0, 9.0), 1))],
            status=status,
        )

        storage.insert_batch(conn, batch, manual_review_reasons=None, image_fingerprint=None)
        inserted += 1

    print(f"Inserted {inserted} realistic medicine batches (skipped {80 - inserted} already present).")


if __name__ == "__main__":
    init_db()
    conn = get_connection()
    generate_realistic_batches(conn)
