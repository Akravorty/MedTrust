import { BatchDecision, QAResponse, TraceEvent } from '../types/schema';

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

// ── QA agent — calls the real Gemini tool-using agent, not Groq ────────────
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
      answer: `❌ **Agent error:** ${msg}\n\nCheck that the backend is running and GEMINI_API_KEY is set in its .env, then retry.`,
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

export const getAlerts = async (batchId: string): Promise<AlertRecord[]> => {
  const res = await fetch(`${API_BASE}/alerts/${encodeURIComponent(batchId)}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.alerts ?? [];
};