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