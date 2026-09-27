"""services/queue/router.py"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.database import get_db
from shared.schemas import QueueStatus
from services.patients.service import PatientNotFoundError
from services.queue.service import (
    TicketNotFoundError,
    advance_ticket_status,
    get_ticket_or_raise,
    join_queue,
    list_queue,
)

router = APIRouter(prefix="/queue", tags=["queue"])


def _db_dependency():
    with get_db() as conn:
        yield conn


class JoinQueueRequest(BaseModel):
    facility_id: str
    patient_id: str
    priority: bool = False


class AdvanceStatusRequest(BaseModel):
    status: QueueStatus
    actor: str


@router.post("")
def post_join_queue(body: JoinQueueRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        ticket = join_queue(db, facility_id=body.facility_id, patient_id=body.patient_id, priority=body.priority)
    except PatientNotFoundError:
        raise HTTPException(status_code=404, detail="Patient not found")
    return ticket.model_dump(mode="json")


@router.get("/{facility_id}")
def get_facility_queue(facility_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    tickets = list_queue(db, facility_id)
    return [t.model_dump(mode="json") for t in tickets]


@router.get("/ticket/{ticket_id}")
def get_one_ticket(ticket_id: str, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        ticket = get_ticket_or_raise(db, ticket_id)
    except TicketNotFoundError:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket.model_dump(mode="json")


@router.patch("/ticket/{ticket_id}/status")
def patch_status(ticket_id: str, body: AdvanceStatusRequest, db: sqlite3.Connection = Depends(_db_dependency)):
    try:
        ticket = advance_ticket_status(db, ticket_id, body.status, body.actor)
    except TicketNotFoundError:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket.model_dump(mode="json")
