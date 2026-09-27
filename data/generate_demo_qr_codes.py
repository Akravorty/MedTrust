# data/generate_demo_qr_codes.py
"""
Generates printable QR code images in real GS1 Application Identifier
format for a handful of the seeded medicine batches, so you have
something authentic to point the webcam at during the demo video instead
of a QR encoding your own private schema.

Usage:
    python data/generate_demo_qr_codes.py

Outputs PNGs to data/demo_qr_codes/. Print these (or display on a second
phone/tablet) and scan them live in the "photograph the pack" flow, or
via the webcam live-scan for the ID-lookup path if you first also print
the plain batch_id as a fallback QR (see --plain flag).

IMPORTANT CAVEAT for the demo pitch: this generates GS1-*formatted* codes
using invented (but validly-structured) GTIN prefixes — it does NOT pull
from any real manufacturer's actual registered GTIN. Be upfront about
that if asked; the point is to demonstrate the parser handles the real
standard, not to claim these are real manufacturer codes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import qrcode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.database import get_connection, init_db
from services.intake import storage

OUT_DIR = Path(__file__).resolve().parent / "demo_qr_codes"


def _yymmdd(d) -> str:
    return d.strftime("%y%m%d")


def build_gs1_payload(batch) -> str:
    """Builds a bracketed-AI GS1 string: (01)GTIN(17)expiry(11)mfgdate(10)batch
    A fabricated but validly-formed GTIN (starts with a placeholder 890
    India GS1 prefix) is used since we don't have a real manufacturer's
    registered prefix — this is disclosed in the module docstring."""
    gtin = f"890{abs(hash(batch.supplier_id)) % 10**11:011d}"
    parts = [f"(01){gtin}"]
    if batch.expiry_date:
        parts.append(f"(17){_yymmdd(batch.expiry_date)}")
    if batch.manufacture_date:
        parts.append(f"(11){_yymmdd(batch.manufacture_date)}")
    parts.append(f"(10){batch.batch_number}")
    return "".join(parts)


def generate(limit: int, plain: bool) -> None:
    init_db()
    conn = get_connection()
    storage.ensure_schema(conn)

    rows = conn.execute(
        "SELECT batch_id FROM intake_batches ORDER BY batch_id LIMIT ?", (limit,)
    ).fetchall()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_lines = ["batch_id,medicine_name,status,file"]

    for row in rows:
        batch = storage.get_batch(conn, row["batch_id"])
        if batch is None:
            continue

        payload = batch.batch_id if plain else build_gs1_payload(batch)
        img = qrcode.make(payload)
        filename = f"{batch.batch_id}.png"
        img.save(OUT_DIR / filename)
        manifest_lines.append(f"{batch.batch_id},{batch.medicine_name},{batch.status.value},{filename}")
        print(f"  {batch.batch_id}  [{batch.status.value}]  -> {filename}")

    (OUT_DIR / "manifest.csv").write_text("\n".join(manifest_lines), encoding="utf-8")
    print(f"\nWrote {len(rows)} QR images + manifest.csv to {OUT_DIR}")
    print("Print a few of each status (ACCEPTED/HOLD/REJECTED) for the demo walkthrough.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=15, help="how many batches to generate QR codes for")
    parser.add_argument("--plain", action="store_true", help="encode the raw batch_id instead of GS1 format (for the live webcam ID-lookup path)")
    args = parser.parse_args()
    generate(args.limit, args.plain)
