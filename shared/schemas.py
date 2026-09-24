from pydantic import BaseModel
from datetime import datetime, date
from enum import Enum
from typing import Optional

class BatchStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    HOLD = "HOLD"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class Decision(str, Enum):
    """The risk engine's decision vocabulary.

    Deliberately distinct from BatchStatus: a decision is what the engine
    concluded, a status is what the batch row records. They are spelled
    differently (ACCEPT vs ACCEPTED, REJECT vs REJECTED) and must be
    translated through DECISION_TO_STATUS below -- never by passing one
    into the other's constructor.
    """
    ACCEPT = "ACCEPT"
    HOLD = "HOLD"
    REJECT = "REJECT"


DECISION_TO_STATUS: dict[str, BatchStatus] = {
    Decision.ACCEPT: BatchStatus.ACCEPTED,
    Decision.HOLD: BatchStatus.HOLD,
    Decision.REJECT: BatchStatus.REJECTED,
}

class TempLogEntry(BaseModel):
    timestamp: datetime
    temp_c: float

class Batch(BaseModel):
    batch_id: str
    medicine_name: str
    batch_number: str
    supplier_id: str
    qr_payload: Optional[str] = None
    ocr_extracted_text: Optional[dict] = None
    ocr_qr_match_score: Optional[float] = None
    manufacture_date: Optional[date] = None
    expiry_date: Optional[date] = None
    received_timestamp: datetime
    storage_temp_log: list[TempLogEntry] = []
    physical_inspection_notes: Optional[str] = None
    status: BatchStatus = BatchStatus.PENDING

class Supplier(BaseModel):
    supplier_id: str
    name: str
    reject_rate_3mo: float = 0.0
    reject_rate_6mo: float = 0.0
    total_batches_supplied: int = 0
    flagged_incidents: list[str] = []

class ShapContributor(BaseModel):
    feature: str
    display_label: str
    contribution: float
    direction: str

class RiskDecision(BaseModel):
    batch_id: str
    risk_score: float
    decision: str
    triggered_rule: Optional[str] = None
    shap_contributors: list[ShapContributor]
    reasons: list[str]
    decided_at: datetime
    model_version: str

class AuditEvent(BaseModel):
    event_id: str
    batch_id: str
    actor: str
    action: str
    payload_hash: str
    prev_hash: str
    this_hash: str
    timestamp: datetime

class RecallNotice(BaseModel):
    batch_id: str
    affected_departments: list[str]
    recipients: list[str]
    generated_notice_text: str
    triggered_by: str
    triggered_at: datetime

class AgentResponse(BaseModel):
    query: str
    answer: str
    evidence_sources: list[str]
    confidence: str

class SupplierAlert(BaseModel):
    supplier_id: str
    trend_description: str
    severity: str
    suggested_action: str
    draft_escalation_message: str


# ---------------------------------------------------------------- care access (new) --

class FacilityLevel(str, Enum):
    SUB_CENTRE = "SUB_CENTRE"
    PHC = "PHC"
    CHC = "CHC"
    RURAL_HOSPITAL = "RURAL_HOSPITAL"
    DISTRICT_HOSPITAL = "DISTRICT_HOSPITAL"


class UrgencyBand(str, Enum):
    ROUTINE = "ROUTINE"
    SOON = "SOON"
    URGENT = "URGENT"
    EMERGENCY = "EMERGENCY"


class ReferralStatus(str, Enum):
    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    IN_TRANSIT = "IN_TRANSIT"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class QueueStatus(str, Enum):
    WAITING = "WAITING"
    CALLED = "CALLED"
    IN_CONSULT = "IN_CONSULT"
    DONE = "DONE"
    NO_SHOW = "NO_SHOW"


class RiskCategory(str, Enum):
    MATERNAL = "MATERNAL"
    CHILD = "CHILD"
    CHRONIC = "CHRONIC"
    NONE = "NONE"


class Facility(BaseModel):
    facility_id: str
    name: str
    level: FacilityLevel
    village_or_area: str
    district: str
    staff_count: int = 0
    beds_total: int = 0
    beds_occupied: int = 0
    has_teleconsult: bool = True


class Patient(BaseModel):
    patient_id: str
    name: str
    age: int
    gender: str
    village: str
    phone: Optional[str] = None
    home_facility_id: str
    risk_category: RiskCategory = RiskCategory.NONE
    registered_by: str  # ASHA / worker id or name
    registered_at: datetime


class TriageResult(BaseModel):
    triage_id: str
    patient_id: str
    symptoms_text: str
    urgency: UrgencyBand
    suggested_facility_level: FacilityLevel
    reasoning: str
    evidence_sources: list[str]
    confidence: str
    decided_at: datetime
    model_version: str = "triage-agent-v1"


class Referral(BaseModel):
    referral_id: str
    patient_id: str
    from_facility_id: str
    to_facility_id: str
    reason: str
    urgency: UrgencyBand
    status: ReferralStatus = ReferralStatus.CREATED
    created_by: str
    created_at: datetime
    updated_at: datetime


class QueueTicket(BaseModel):
    ticket_id: str
    facility_id: str
    patient_id: str
    token_number: int
    status: QueueStatus = QueueStatus.WAITING
    priority: bool = False  # emergency-escalated tickets jump the line
    created_at: datetime
    called_at: Optional[datetime] = None
    est_wait_minutes: Optional[int] = None


class TeleconsultSession(BaseModel):
    session_id: str
    patient_id: str
    referral_id: Optional[str] = None
    facility_id: str
    doctor_name: str
    status: str = "SCHEDULED"  # SCHEDULED | ACTIVE | COMPLETED
    notes: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class FollowUp(BaseModel):
    follow_up_id: str
    patient_id: str
    risk_category: RiskCategory
    reason: str
    due_date: date
    completed: bool = False
    completed_at: Optional[datetime] = None
    created_by: str
    created_at: datetime