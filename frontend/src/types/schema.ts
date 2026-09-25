export type Status = 'ACCEPT' | 'HOLD' | 'REJECT' | 'RECALL' | 'MANUAL_REVIEW';

export interface ShapFeature {
  feature_name: string;
  display_label: string;
  value: number;
}

export interface BatchDecision {
  batch_id: string;
  status: Status;
  confidence: number;
  features: ShapFeature[];
  timestamp: string;
  // Added for Step 6 — present on every real /risk/evaluate response.
  // Optional so existing mocks/tests that don't set them still type-check.
  risk_score?: number;
  triggered_rule?: string | null;
  reasons?: string[];
  model_version?: string;
}

export interface QAResponse {
  answer: string;
  confidence: number;
  evidence_sources: string[];
}

export interface TraceEvent {
  id: string;
  timestamp: string;
  location: string;
  action: string;
  verified: boolean;
}

export interface SupplierAlert {
  supplier_id: string;
  severity: 'LOW' | 'MEDIUM' | 'HIGH';
  message: string;
}

// ═══════════════════════════════════════════════════════════════════════
// Care-Access module (SIH26133 pivot) — patients, triage, referrals,
// queue, teleconsult, follow-ups, facility dashboard.
// Types mirror the backend's Pydantic schemas field-for-field.
// ═══════════════════════════════════════════════════════════════════════

export type FacilityLevel = 'SUB_CENTRE' | 'PHC' | 'CHC' | 'RURAL_HOSPITAL' | 'DISTRICT_HOSPITAL';
export type UrgencyBand = 'ROUTINE' | 'SOON' | 'URGENT' | 'EMERGENCY';
export type ReferralStatus = 'CREATED' | 'ACCEPTED' | 'IN_TRANSIT' | 'COMPLETED' | 'CANCELLED';
export type QueueStatus = 'WAITING' | 'CALLED' | 'IN_CONSULT' | 'DONE' | 'NO_SHOW';
export type RiskCategory = 'MATERNAL' | 'CHILD' | 'CHRONIC' | 'NONE';
export type TriageConfidence = 'HIGH' | 'MEDIUM' | 'INSUFFICIENT_EVIDENCE';
export type TeleconsultStatus = 'SCHEDULED' | 'ACTIVE' | 'COMPLETED';

export interface Facility {
  facility_id: string;
  name: string;
  level: FacilityLevel;
  village_or_area: string;
  district: string;
  staff_count: number;
  beds_total: number;
  beds_occupied: number;
  has_teleconsult: boolean;
}

export interface Patient {
  patient_id: string;
  name: string;
  age: number;
  gender: string;
  village: string;
  phone: string | null;
  home_facility_id: string;
  risk_category: RiskCategory;
  registered_by: string;
  registered_at: string; // ISO datetime
}

export interface TriageResult {
  triage_id: string;
  patient_id: string;
  symptoms_text: string;
  urgency: UrgencyBand;
  suggested_facility_level: FacilityLevel;
  reasoning: string;
  evidence_sources: string[];
  confidence: TriageConfidence;
  decided_at: string;
  model_version: string;
}

export interface Referral {
  referral_id: string;
  patient_id: string;
  from_facility_id: string;
  to_facility_id: string;
  reason: string;
  urgency: UrgencyBand;
  status: ReferralStatus;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface QueueTicket {
  ticket_id: string;
  facility_id: string;
  patient_id: string;
  token_number: number;
  status: QueueStatus;
  priority: boolean;
  created_at: string;
  called_at: string | null;
  est_wait_minutes: number | null;
}

export interface TeleconsultSession {
  session_id: string;
  patient_id: string;
  referral_id: string | null;
  facility_id: string;
  doctor_name: string;
  status: TeleconsultStatus;
  notes: string | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface FollowUp {
  follow_up_id: string;
  patient_id: string;
  risk_category: RiskCategory;
  reason: string;
  due_date: string; // YYYY-MM-DD
  completed: boolean;
  completed_at: string | null;
  created_by: string;
  created_at: string;
}

export interface PatientTimelineEvent {
  event_id: string;
  actor: string;
  action: string;
  timestamp: string;
}

export interface PatientTimeline {
  patient_id: string;
  events: PatientTimelineEvent[];
}

export interface PatientVerification {
  patient_id: string;
  valid: boolean;
  total_events: number;
  explanation: string;
}

export interface FacilityDashboardData {
  facility_id: string;
  date: string;
  patients_registered_today: number;
  patients_triaged_today: number;
  emergency_flags_today: number;
  referrals_completed_total: number;
  referrals_pending_incoming: number;
  queue_depth_now: number;
  high_risk_follow_ups_overdue: number;
  medicine_availability: {
    total_batches_tracked: number;
    pass_rate_pct: number | null;
    flagged: number;
  };
}