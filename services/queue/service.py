"""
services/queue/service.py

Facility-level appointment/token queue. Tickets are issued per facility
per day (token numbers reset daily), FIFO by default, with a `priority`
flag that lets an EMERGENCY-triaged patient jump ahead of routine tickets
without needing a human to manually reorder anyone. Wait-time estimate is
a simple average-consult-time * position heuristic — documented as such,
not dressed up as a scheduling optimizer.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime, timezone

from services.ledger.chain import append_event
from services.patients.service import get_patient_or_raise
from shared.schemas import QueueStatus, QueueTicket

ACTION_QUEUE_JOINED = "QUEUE_JOINED"
ACTION_QUEUE_STATUS_CHANGED = "QUEUE_STATUS_CHANGED"

# Heuristic average minutes per consult, used only for the wait-time
# estimate shown to the patient/ASHA — not a hard scheduling guarantee.
_AVG_CONSULT_MINUTES = 8


def ensure_queue_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_tickets (
            ticket_id      TEXT PRIMARY KEY,
            facility_id    TEXT NOT NULL,
            patient_id     TEXT NOT NULL,
            token_number   INTEGER NOT NULL,
            status         TEXT NOT NULL DEFAULT 'WAITING',
            priority       INTEGER NOT NULL DEFAULT 0,
            queue_date     TEXT NOT NULL,
            created_at     TEXT NOT NULL,
            called_at      TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_queue_facility_date ON queue_tickets (facility_id, queue_date, status)"
    )


def _today() -> str:
    return date.today().isoformat()


def join_queue(
    conn: sqlite3.Connection, *, facility_id: str, patient_id: str, priority: bool = False
) -> QueueTicket:
    get_patient_or_raise(conn, patient_id)
    today = _today()

    row = conn.execute(
        "SELECT COALESCE(MAX(token_number), 0) + 1 AS next_token FROM queue_tickets "
        "WHERE facility_id = ? AND queue_date = ?",
        (facility_id, today),
    ).fetchone()
    token_number = row["next_token"]

    ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now(timezone.utc)

    conn.execute(
        """
        INSERT INTO queue_tickets
            (ticket_id, facility_id, patient_id, token_number, status, priority, queue_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ticket_id, facility_id, patient_id, token_number, QueueStatus.WAITING.value,
         int(priority), today, now.isoformat()),
    )
    conn.commit()

    append_event(
        conn, batch_id=patient_id, actor=patient_id, action=ACTION_QUEUE_JOINED,
        payload={"ticket_id": ticket_id, "facility_id": facility_id, "token_number": token_number, "priority": priority},
    )

    return get_ticket_or_raise(conn, ticket_id)


class QueueError(Exception):
    """Base for named queue errors."""


class TicketNotFoundError(QueueError):
    def __init__(self):
        super().__init__("Queue ticket not found")


def get_ticket(conn: sqlite3.Connection, ticket_id: str) -> QueueTicket | None:
    row = conn.execute("SELECT * FROM queue_tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if row is None:
        return None
    return _row_to_ticket(row)


def get_ticket_or_raise(conn: sqlite3.Connection, ticket_id: str) -> QueueTicket:
    ticket = get_ticket(conn, ticket_id)
    if ticket is None:
        raise TicketNotFoundError()
    return _attach_wait_estimate(conn, ticket)


def list_queue(conn: sqlite3.Connection, facility_id: str, queue_date: str | None = None) -> list[QueueTicket]:
    """Ordered exactly as patients will actually be called: priority
    tickets first, then FIFO by token number, within today's queue."""
    qd = queue_date or _today()
    rows = conn.execute(
        """
        SELECT * FROM queue_tickets
        WHERE facility_id = ? AND queue_date = ? AND status IN ('WAITING', 'CALLED', 'IN_CONSULT')
        ORDER BY priority DESC, token_number ASC
        """,
        (facility_id, qd),
    ).fetchall()
    tickets = [_row_to_ticket(r) for r in rows]
    return [_attach_wait_estimate(conn, t, position=i) for i, t in enumerate(tickets)]


def _attach_wait_estimate(conn: sqlite3.Connection, ticket: QueueTicket, position: int | None = None) -> QueueTicket:
    if ticket.status != QueueStatus.WAITING:
        ticket.est_wait_minutes = 0
        return ticket
    if position is None:
        # Recompute this ticket's position among still-waiting tickets ahead of it.
        row = conn.execute(
            """
            SELECT COUNT(*) AS ahead FROM queue_tickets
            WHERE facility_id = ? AND queue_date = ? AND status = 'WAITING'
              AND (priority > ? OR (priority = ? AND token_number < ?))
            """,
            (ticket.facility_id, _today(), int(ticket.priority), int(ticket.priority), ticket.token_number),
        ).fetchone()
        position = row["ahead"]
    ticket.est_wait_minutes = position * _AVG_CONSULT_MINUTES
    return ticket


def advance_ticket_status(conn: sqlite3.Connection, ticket_id: str, new_status: QueueStatus, actor: str) -> QueueTicket:
    ticket = get_ticket(conn, ticket_id)
    if ticket is None:
        raise TicketNotFoundError()

    called_at_clause, params = "", [new_status.value]
    if new_status == QueueStatus.CALLED:
        called_at_clause = ", called_at = ?"
        params.append(datetime.now(timezone.utc).isoformat())
    params.append(ticket_id)

    conn.execute(
        f"UPDATE queue_tickets SET status = ?{called_at_clause} WHERE ticket_id = ?", params
    )
    conn.commit()

    append_event(
        conn, batch_id=ticket.patient_id, actor=actor, action=ACTION_QUEUE_STATUS_CHANGED,
        payload={"ticket_id": ticket_id, "from_status": ticket.status.value, "to_status": new_status.value},
    )

    return get_ticket_or_raise(conn, ticket_id)


def _row_to_ticket(row: sqlite3.Row) -> QueueTicket:
    return QueueTicket(
        ticket_id=row["ticket_id"], facility_id=row["facility_id"], patient_id=row["patient_id"],
        token_number=row["token_number"], status=QueueStatus(row["status"]), priority=bool(row["priority"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        called_at=datetime.fromisoformat(row["called_at"]) if row["called_at"] else None,
    )
