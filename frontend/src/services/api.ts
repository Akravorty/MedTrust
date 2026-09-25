import {
  BatchDecision, QAResponse, TraceEvent,
  Facility, Patient, TriageResult, Referral, QueueTicket, TeleconsultSession,
  FollowUp, PatientTimeline, PatientVerification, FacilityDashboardData,
  ReferralStatus, QueueStatus, RiskCategory, DiagnosticOrder, DiagnosticResultFlag,
} from '../types/schema';

const API_BASE = import.meta.env.VITE_API_BASE as string;

// Full English names, so the model reliably knows what "mr" or "or" means -
// matches the `name` field in frontend/src/i18n.tsx.
const AGENT_LANGUAGE_NAMES: Record<string, string> = {
  en: 'English', hi: 'Hindi', mr: 'Marathi', or: 'Odia', bn: 'Bengali',
  ta: 'Tamil', te: 'Telugu', kn: 'Kannada', ml: 'Malayalam', gu: 'Gujarati',
  pa: 'Punjabi', as: 'Assamese', ur: 'Urdu', ne: 'Nepali', sa: 'Sanskrit',
  sd: 'Sindhi', kok: 'Konkani', mai: 'Maithili', doi: 'Dogri', brx: 'Bodo',
  ks: 'Kashmiri', mni: 'Manipuri', sat: 'Santali',
};

// ── Risk evaluation (was: hash-the-string mock) ─────────────────────────────
export const scanBatch = async (
  batchId: string,
  idempotencyKey?: string,
): Promise<BatchDecision> => {
  const res = await fetch(`${API_BASE}/risk/evaluate/${encodeURIComponent(batchId)}`, {
    method: 'POST',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || `Risk evaluation failed (${res.status})`) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }

  const d = await res.json();

  return {
    batch_id: d.batch_id,
    status: d.decision,                      // "ACCEPT" | "HOLD" | "REJECT"
    confidence: Number((1 - d.risk_score).toFixed(2)),
    risk_score: d.risk_score,
    triggered_rule: d.triggered_rule,
    reasons: d.reasons,
    model_version: d.model_version,
    timestamp: d.decided_at,
    features: (d.shap_contributors ?? []).map((c: { feature: string; display_label: string; contribution: number }) => ({
      feature_name: c.feature,
      display_label: c.display_label,
      value: c.contribution,
    })),
  };
};

// ── Pack intake (photo -> registers a new batch via OCR/QR) ─────────────────────────────────
// POST /intake/scan (multipart/form-data). Registers a NEW batch from a
// photo. This is what "typing a batch ID" can never do for a medicine that
// has never been scanned before — that path only looks up existing rows.
export interface IntakeResult {
  batch_id: string;
  medicine_name: string | null;
  batch_number: string | null;
  ocr_qr_match_score: number | null;
  status: string; // "INTAKE_OK" | "MANUAL_REVIEW" | ...
  manual_review_reasons?: string[];
}

export const scanPack = async (file: File | Blob): Promise<IntakeResult> => {
  const form = new FormData();
  form.append('file', file, (file as File).name ?? 'pack.jpg');
  const res = await fetch(`${API_BASE}/intake/scan`, { method: 'POST', body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || `Could not read the pack (${res.status})`) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return res.json();
};

// ── QA agent — calls the real backend tool-using agent (Groq-hosted model) ────────────
// POST /agent/qa only takes {query: string}. It resolves its own evidence
// via get_batch/get_decision/get_trace tools, so the batch_id has to be
// inside the query text itself, not passed as a separate structured field.
const CONFIDENCE_MAP: Record<string, number> = {
  HIGH: 0.95,
  MEDIUM: 0.65,
  INSUFFICIENT_EVIDENCE: 0.3,
};

export const askAgent = async (
  batchData: BatchDecision,
  question: string,
  history: Array<{ role: 'user' | 'agent'; text: string }> = [],
  language: string = 'en',
): Promise<QAResponse> => {
  if (!batchData?.batch_id) {
    return {
      answer: '❌ **System Error:** No valid batch is loaded to ask about.',
      confidence: 0,
      evidence_sources: [],
    };
  }

  const recentHistory = history
    .slice(-6)
    .map(m => `${m.role === 'user' ? 'User' : 'Agent'}: ${m.text}`)
    .join('\n');

  const langName = AGENT_LANGUAGE_NAMES[language] ?? 'English';

  const composedQuery = [
    langName !== 'English' ? `Answer in ${langName}, not English.` : null,
    `The question below is about batch ${batchData.batch_id} (currently shown as ${batchData.status} on screen — verify this against the actual decision record, don't trust it blindly).`,
    recentHistory,
    `User: ${question}`,
  ].filter(Boolean).join('\n');

  try {
    const res = await fetch(`${API_BASE}/agent/qa`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: composedQuery }),
    });

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Agent request failed (${res.status})`);
    }

    const data = await res.json();

    return {
      answer: data.answer ?? 'No response received from agent.',
      confidence: CONFIDENCE_MAP[data.confidence] ?? 0.5,
      evidence_sources: data.evidence_sources ?? [],
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Unknown error';
    return {
      answer: `❌ **Agent error:** ${msg}\n\nCheck that the backend is running and GROQ_API_KEY is set in its .env, then retry.`,
      confidence: 0,
      evidence_sources: [],
    };
  }
};

// ── Ledger trace & chain verification ───────────────────────────────────────
// GET /ledger/trace/{batch_id} returns {events, chain_valid, ...} in one
// call — there's no separate verify endpoint, so verifyChain reads
// chain_valid off the same response rather than hitting a second route.
export const getTrace = async (batchId: string): Promise<TraceEvent[]> => {
  const res = await fetch(`${API_BASE}/ledger/trace/${encodeURIComponent(batchId)}`);

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Failed to load trace (${res.status})`);
  }

  const data = await res.json();

  return data.events.map((e: { event_id: string; timestamp: string; actor: string; action: string }) => ({
    id: e.event_id,
    timestamp: e.timestamp,
    location: e.actor,          // the ledger tracks actors, not physical locations
    action: e.action,
    verified: data.chain_valid,
  }));
};

export const verifyChain = async (batchId: string): Promise<boolean> => {
  const res = await fetch(`${API_BASE}/ledger/trace/${encodeURIComponent(batchId)}`);
  if (!res.ok) return false;
  const data = await res.json();
  return Boolean(data.chain_valid);
};

// ── Recall simulation ───────────────────────────────────────────────────────
export const simulateRecall = async (
  batchId: string,
  triggeredBy: string = 'ui_operator',
): Promise<{ recipients: string[]; affected_departments: string[]; [key: string]: unknown }> => {
  const res = await fetch(`${API_BASE}/ledger/recall/simulate/${encodeURIComponent(batchId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ triggered_by: triggeredBy }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Recall simulation failed (${res.status})`);
  }

  return res.json();
};

// ── Alerts (Step 8) ─────────────────────────────────────────────────────────
// GET /alerts/{batch_id} -> { batch_id, alerts: [...] }, newest first.
export interface AlertRecord {
  alert_id: string;
  batch_id: string;
  recipient: string;
  channel: string;      // "LOG" | "SMS"
  language: string;     // "HI" | "OR" | "EN"
  alert_type: string;   // "HOLD" | "REJECT" | "RECALL"
  message: string;
  status: string;       // "SENT" | "QUEUED"
  created_at: string;
}

export const getAlerts = async (batchId: string, lang?: string): Promise<AlertRecord[]> => {
  const url = lang
    ? `${API_BASE}/alerts/${encodeURIComponent(batchId)}?lang=${encodeURIComponent(lang)}`
    : `${API_BASE}/alerts/${encodeURIComponent(batchId)}`;
  const res = await fetch(url);
  if (!res.ok) return [];
  const data = await res.json();
  return data.alerts ?? [];
};

// GET /alerts/audio/{lang}/{status}/exists -> { exists: boolean }
// Cheap check before rendering a play button, so we never show a control
// for a clip that was never recorded (currently only en/hi/or/mr have any).
export const checkAlertAudioExists = async (lang: string, status: string): Promise<boolean> => {
  try {
    const res = await fetch(`${API_BASE}/alerts/audio/${encodeURIComponent(lang)}/${encodeURIComponent(status)}/exists`);
    if (!res.ok) return false;
    const data = await res.json();
    return !!data.exists;
  } catch {
    return false;
  }
};

// Direct playable URL for GET /alerts/audio/{lang}/{status} — pass straight
// to an <audio> element's src.
export const getAlertAudioUrl = (lang: string, status: string): string =>
  `${API_BASE}/alerts/audio/${encodeURIComponent(lang)}/${encodeURIComponent(status)}`;
// ═══════════════════════════════════════════════════════════════════════
// Care-Access module (SIH26133 pivot) — patients, triage, referrals,
// queue, teleconsult, follow-ups, facility dashboard.
//
// Shared helper so every new endpoint handles non-2xx the same way the
// existing scanBatch/scanPack do: throw an Error carrying `.status`, with
// the backend's `{ detail }` message when present.
// ═══════════════════════════════════════════════════════════════════════
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || `Request failed (${res.status})`) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  // 204s and similar have no body to parse.
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// ── Facilities ───────────────────────────────────────────────────────────
export const listFacilities = (district?: string): Promise<Facility[]> =>
  request(`/facilities${district ? `?district=${encodeURIComponent(district)}` : ''}`);

export const getFacility = (facilityId: string): Promise<Facility> =>
  request(`/facilities/${encodeURIComponent(facilityId)}`);

// ── Patients ─────────────────────────────────────────────────────────────
export interface RegisterPatientInput {
  name: string;
  age: number;
  gender: string;
  village: string;
  home_facility_id: string;
  registered_by: string;
  phone?: string;
}

export const registerPatient = (input: RegisterPatientInput): Promise<Patient> =>
  request('/patients', { method: 'POST', body: JSON.stringify(input) });

export const listPatients = (facilityId?: string): Promise<Patient[]> =>
  request(`/patients${facilityId ? `?facility_id=${encodeURIComponent(facilityId)}` : ''}`);

export const getPatient = (patientId: string): Promise<Patient> =>
  request(`/patients/${encodeURIComponent(patientId)}`);

export const getPatientTimeline = (patientId: string): Promise<PatientTimeline> =>
  request(`/patients/${encodeURIComponent(patientId)}/timeline`);

export const verifyPatientRecord = (patientId: string): Promise<PatientVerification> =>
  request(`/patients/${encodeURIComponent(patientId)}/verify`);

export const updateRiskCategory = (
  patientId: string,
  risk_category: RiskCategory,
  actor: string,
  reason: string,
): Promise<Patient> =>
  request(`/patients/${encodeURIComponent(patientId)}/risk-category`, {
    method: 'PATCH',
    body: JSON.stringify({ risk_category, actor, reason }),
  });

// ── Triage ───────────────────────────────────────────────────────────────
// NOTE: this is a live multi-tool-call Groq agent — it can take several
// seconds. Callers should show a "thinking" state, not a spinner that reads
// as frozen.
export const runTriage = (
  patient_id: string,
  symptoms_text: string,
  actor: string,
): Promise<TriageResult> =>
  request('/triage', { method: 'POST', body: JSON.stringify({ patient_id, symptoms_text, actor }) });

export const getLatestTriage = (patientId: string): Promise<TriageResult> =>
  request(`/triage/${encodeURIComponent(patientId)}/latest`);

// ── Referrals ────────────────────────────────────────────────────────────
export interface CreateReferralInput {
  patient_id: string;
  from_facility_id: string;
  to_facility_id: string;
  reason: string;
  urgency: string;
  created_by: string;
}

export const createReferral = (input: CreateReferralInput): Promise<Referral> =>
  request('/referrals', { method: 'POST', body: JSON.stringify(input) });

export const listReferrals = (params: {
  patient_id?: string; to_facility_id?: string; status?: string;
} = {}): Promise<Referral[]> => {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v) as [string, string][],
  ).toString();
  return request(`/referrals${qs ? `?${qs}` : ''}`);
};

export const getReferral = (referralId: string): Promise<Referral> =>
  request(`/referrals/${encodeURIComponent(referralId)}`);

// Throws with `.status === 409` on an invalid transition — the backend is
// the source of truth here; the UI only pre-filters which buttons it shows.
export const updateReferralStatus = (
  referralId: string,
  status: ReferralStatus,
  actor: string,
): Promise<Referral> =>
  request(`/referrals/${encodeURIComponent(referralId)}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status, actor }),
  });

// ── Queue / token booking ────────────────────────────────────────────────
export const joinQueue = (
  facility_id: string,
  patient_id: string,
  priority: boolean = false,
): Promise<QueueTicket> =>
  request('/queue', { method: 'POST', body: JSON.stringify({ facility_id, patient_id, priority }) });

export const getQueue = (facilityId: string): Promise<QueueTicket[]> =>
  request(`/queue/${encodeURIComponent(facilityId)}`);

export const getQueueTicket = (ticketId: string): Promise<QueueTicket> =>
  request(`/queue/ticket/${encodeURIComponent(ticketId)}`);

export const updateQueueTicketStatus = (
  ticketId: string,
  status: QueueStatus,
  actor: string,
): Promise<QueueTicket> =>
  request(`/queue/ticket/${encodeURIComponent(ticketId)}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status, actor }),
  });

// ── Teleconsult ──────────────────────────────────────────────────────────
export const scheduleTeleconsult = (
  patient_id: string,
  facility_id: string,
  doctor_name: string,
  referral_id?: string,
): Promise<TeleconsultSession> =>
  request('/teleconsult', {
    method: 'POST',
    body: JSON.stringify({ patient_id, facility_id, doctor_name, referral_id }),
  });

export const startTeleconsult = (sessionId: string): Promise<TeleconsultSession> =>
  request(`/teleconsult/${encodeURIComponent(sessionId)}/start`, { method: 'POST' });

export const completeTeleconsult = (sessionId: string, notes: string): Promise<TeleconsultSession> =>
  request(`/teleconsult/${encodeURIComponent(sessionId)}/complete`, {
    method: 'POST',
    body: JSON.stringify({ notes }),
  });

export const listTeleconsultForPatient = (patientId: string): Promise<TeleconsultSession[]> =>
  request(`/teleconsult/patient/${encodeURIComponent(patientId)}`);

export const getTeleconsult = (sessionId: string): Promise<TeleconsultSession> =>
  request(`/teleconsult/${encodeURIComponent(sessionId)}`);

// ── Follow-ups ───────────────────────────────────────────────────────────
export interface CreateFollowUpInput {
  patient_id: string;
  risk_category: RiskCategory;
  reason: string;
  due_date: string;
  created_by: string;
}

export const createFollowUp = (input: CreateFollowUpInput): Promise<FollowUp> =>
  request('/followups', { method: 'POST', body: JSON.stringify(input) });

export const listFollowUps = (params: {
  patient_id?: string; overdue_only?: boolean;
} = {}): Promise<FollowUp[]> => {
  const qs = new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => [k, String(v)]),
  ).toString();
  return request(`/followups${qs ? `?${qs}` : ''}`);
};

export const completeFollowUp = (followUpId: string, actor: string): Promise<FollowUp> =>
  request(`/followups/${encodeURIComponent(followUpId)}/complete`, {
    method: 'POST',
    body: JSON.stringify({ actor }),
  });

// ── Diagnostics ──────────────────────────────────────────────────────────
// POST /diagnostics auto-routes to the nearest diagnostics-capable facility
// when the ordering facility can't run the test itself — `routed` on the
// response says whether that happened, so the UI can tell the health worker
// where the sample is actually going.
export interface CreateDiagnosticOrderInput {
  patient_id: string;
  ordering_facility_id: string;
  test_type: string;
  reason: string;
  ordered_by: string;
}

export const createDiagnosticOrder = (input: CreateDiagnosticOrderInput): Promise<DiagnosticOrder> =>
  request('/diagnostics', { method: 'POST', body: JSON.stringify(input) });

export const listDiagnosticOrders = (params: {
  patient_id?: string; performing_facility_id?: string; status?: string;
} = {}): Promise<DiagnosticOrder[]> => {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v) as [string, string][],
  ).toString();
  return request(`/diagnostics${qs ? `?${qs}` : ''}`);
};

export const getDiagnosticOrder = (diagnosticId: string): Promise<DiagnosticOrder> =>
  request(`/diagnostics/${encodeURIComponent(diagnosticId)}`);

export const markSampleCollected = (diagnosticId: string, actor: string): Promise<DiagnosticOrder> =>
  request(`/diagnostics/${encodeURIComponent(diagnosticId)}/sample-collected`, {
    method: 'POST',
    body: JSON.stringify({ actor }),
  });

export const recordDiagnosticResult = (
  diagnosticId: string,
  result_flag: DiagnosticResultFlag,
  result_summary: string,
  actor: string,
): Promise<DiagnosticOrder> =>
  request(`/diagnostics/${encodeURIComponent(diagnosticId)}/result`, {
    method: 'POST',
    body: JSON.stringify({ result_flag, result_summary, actor }),
  });

export const reviewDiagnosticResult = (diagnosticId: string, actor: string): Promise<DiagnosticOrder> =>
  request(`/diagnostics/${encodeURIComponent(diagnosticId)}/review`, {
    method: 'POST',
    body: JSON.stringify({ actor }),
  });

export const cancelDiagnosticOrder = (diagnosticId: string, actor: string, reason?: string): Promise<DiagnosticOrder> =>
  request(`/diagnostics/${encodeURIComponent(diagnosticId)}/cancel`, {
    method: 'POST',
    body: JSON.stringify({ actor, reason }),
  });

// ── Dashboard ────────────────────────────────────────────────────────────
export const getDashboard = (facilityId: string): Promise<FacilityDashboardData> =>
  request(`/dashboard/${encodeURIComponent(facilityId)}`);
