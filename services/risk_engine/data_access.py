"""
services/risk_engine/data_access.py

ASSUMPTION TO VERIFY AGAINST THE REAL REPO BEFORE FIRST RUN:
This file assumes shared/database.py exposes something like:

    get_connection() -> sqlite3.Connection
    get_batch(conn, batch_id: str) -> Optional[Batch]
    get_supplier(conn, supplier_id: str) -> Optional[Supplier]
    update_batch_status(conn, batch_id: str, status: BatchStatus) -> None

These function names are a REASONABLE GUESS consistent with the
"single source of truth" pattern described in the master doc (each service
calls shared/database.py rather than writing its own DB layer). They may not
match exactly what Person 2 already implemented.

DO NOT silently rewrite shared/database.py to match this file. Instead:
1. Open the real shared/database.py.
2. If the function names differ, edit ONLY the four lines below marked
   "ADAPT HERE" to call the real functions.
3. If the needed functions don't exist yet at all, flag it to the team
   (per Section 10) rather than adding them yourself, since Person 2's
   intake module likely already owns Batch creation/retrieval.
"""

from __future__ import annotations

from typing import Optional

from shared.schemas import Batch, BatchStatus, Supplier

try:
    from shared.database import get_connection  # noqa: F401
except ImportError:
    get_connection = None  # allows this module to be imported/tested before
                            # shared/database.py exists in a given checkout


def fetch_batch(batch_id: str) -> Optional[Batch]:
    # ADAPT HERE if the real function name/signature differs.
    from shared.database import get_batch as _get_batch  # local import: keep
                                                           # this file safe to
                                                           # import even if
                                                           # shared/database.py
                                                           # is mid-edit.
    conn = get_connection()
    return _get_batch(conn, batch_id)


def fetch_supplier(supplier_id: str) -> Optional[Supplier]:
    # ADAPT HERE if the real function name/signature differs.
    from shared.database import get_supplier as _get_supplier
    conn = get_connection()
    return _get_supplier(conn, supplier_id)


def persist_batch_status(batch_id: str, status: BatchStatus) -> None:
    # ADAPT HERE if the real function name/signature differs.
    from shared.database import update_batch_status as _update_batch_status
    conn = get_connection()
    _update_batch_status(conn, batch_id, status)
