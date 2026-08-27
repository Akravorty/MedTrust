# data/generate_suppliers.py
from shared.database import get_connection

def ensure_suppliers_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS suppliers (
            supplier_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            reject_rate_3mo REAL NOT NULL DEFAULT 0.0,
            reject_rate_6mo REAL NOT NULL DEFAULT 0.0,
            total_batches_supplied INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()

def generate_suppliers(conn) -> None:
    print("Generating synthetic suppliers...")
    ensure_suppliers_schema(conn)

    suppliers = [
        ("S-DEMO", "Demo Pharma Supplies", 0.02, 0.03, 120),
        ("S-GOOD", "Reliable Meds Co", 0.01, 0.015, 340),
        ("S-RISKY", "Fast Track Distributors", 0.18, 0.22, 45),
    ]

    for supplier_id, name, r3, r6, total in suppliers:
        conn.execute(
            """
            INSERT OR REPLACE INTO suppliers
                (supplier_id, name, reject_rate_3mo, reject_rate_6mo, total_batches_supplied)
            VALUES (?, ?, ?, ?, ?)
            """,
            (supplier_id, name, r3, r6, total),
        )
    conn.commit()
    print(f"Inserted {len(suppliers)} suppliers.")

if __name__ == "__main__":
    conn = get_connection()
    generate_suppliers(conn)