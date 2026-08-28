import { BatchDecision, QAResponse, TraceEvent, Status } from '../types/schema';

// Mock delays for scan/trace endpoints
const delay = (ms: number) => new Promise(res => setTimeout(res, ms));

// ── Deterministic risk generator ─────────────────────────────────────────────
export const getBatchRiskData = (batchId: string, forceStatus?: Status): BatchDecision => {
  const upper = batchId.toUpperCase();

  let status: Status = 'HOLD';
  let confidence = 0.64;

  if (forceStatus) {
    status = forceStatus;
    confidence = status === 'ACCEPT' ? 0.96 : status === 'REJECT' ? 0.98 : 0.64;
  } else if (upper.includes('702') || upper.includes('ACCEPT') || upper.includes('PASS') || upper.includes('334') || upper.includes('289')) {
    status = 'ACCEPT';
    confidence = 0.94;
  } else if (upper.includes('410') || upper.includes('REJECT') || upper.includes('FAIL')) {
    status = 'REJECT';
    confidence = 0.97;
  } else if (upper.includes('RECALL')) {
    status = 'RECALL';
    confidence = 0.99;
  } else {
    let hash = 0;
    for (let i = 0; i < upper.length; i++) {
      hash = (hash << 5) - hash + upper.charCodeAt(i);
      hash |= 0;
    }
    const absHash = Math.abs(hash);
    const mod = absHash % 3;
    status = mod === 0 ? 'HOLD' : mod === 1 ? 'ACCEPT' : 'REJECT';
    confidence = 0.62 + (absHash % 35) / 100;
  }

  const features = [
    { feature_name: 'temp_exc',       display_label: 'Temperature excursion',    value: status === 'REJECT' ? 0.78 : status === 'HOLD' ? 0.38 : 0.04 },
    { feature_name: 'supp_trend',     display_label: 'Supplier trend deviation', value: status === 'REJECT' ? 0.62 : status === 'HOLD' ? 0.22 : 0.08 },
    { feature_name: 'viscosity_drift',display_label: 'Viscosity & pH drift',     value: status === 'REJECT' ? 0.54 : status === 'HOLD' ? 0.16 : 0.02 },
    { feature_name: 'ocr_mismatch',   display_label: 'OCR / Seal integrity',     value: status === 'REJECT' ? 0.45 : status === 'HOLD' ? 0.11 : 0.01 },
  ];

  return {
    batch_id: batchId,
    status,
    confidence: Number(confidence.toFixed(2)),
    timestamp: new Date().toISOString(),
    features,
  };
};

export const scanBatch = async (batchId: string, forceStatus?: Status): Promise<BatchDecision> => {
  await delay(600);
  return getBatchRiskData(batchId, forceStatus);
};

// ── Real backend evidence-grounded agent: POST /agent/qa ────────────────────
export const askAgent = async (
  batchData: BatchDecision,
  question: string,
): Promise<QAResponse> => {
  if (!batchData || !batchData.batch_id) {
    return {
      answer: 'No batch is currently selected — scan or select a batch first.',
      confidence: 'INSUFFICIENT_EVIDENCE',
      evidence_sources: [],
    };
  }

  // Scope the question to the current batch, matching how qa_agent.py's
  // tools (get_batch/get_decision/get_trace) expect to be queried.
  const scopedQuery = `Regarding batch ${batchData.batch_id}: ${question}`;

  try {
    const res = await fetch(`${API_BASE}/agent/qa`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: scopedQuery }),
    });

    if (!res.ok) {
      const errBody = await res.text().catch(() => '');
      throw new Error(`Agent unavailable (${res.status}): ${errBody}`);
    }

    const data = await res.json();
    return {
      answer: data.answer,
      confidence: data.confidence,
      evidence_sources: data.evidence_sources ?? [],
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Unknown error';
    return {
      answer: `❌ **Agent error:** ${msg}`,
      confidence: 'INSUFFICIENT_EVIDENCE',
      evidence_sources: [],
    };
  }
};
  // ── Guard: no batch data ──────────────────────────────────────────────────
  if (!batchData || !batchData.batch_id) {
    return {
      answer: '❌ **System Error:** No valid Batch ID was provided in the input parameters. Please provide a valid batch to proceed.',
      confidence: 0,
      evidence_sources: [],
    };
  }

  // ── Build system prompt with live batch context ──────────────────────────
  const systemPrompt = `You are the MediTrust QA Agent. Your role is to explain batch quality decisions strictly using the provided active payload state.

INPUT VALIDATION:
- ALWAYS ensure \`batchId\` is defined in the input parameters. 
- If \`batchId\` is missing, return a clean structural error asking the system to provide a valid Batch ID.

STATE-BASED RESPONSE RULES:
1. Detect \`batch.status\` from the payload and structure the response accordingly:

   • STATUS = "ACCEPT":
     - State that the batch passed quality standards.
     - Confirm key SHAP risk factors (Temperature Excursion, Supplier Trend, Viscosity/pH Drift, OCR/Seal Integrity) are at safe, minimal levels (below 0.08).
     - State the high confidence score (e.g., 96.0%).

   • STATUS = "REJECT":
     - State that the batch failed quality standards.
     - Highlight specific elevated risk scores triggering the rejection (e.g., Temperature Excursion > 0.70).

   • STATUS = "HOLD":
     - State that the batch is quarantined pending manual inspector review.
     - Detail border-line or conflicting risk parameters requiring secondary verification.

2. NEVER hallucinate metrics or status values from a different state.

INPUT CONTEXT:
- Batch ID: ${batchData.batch_id}
- Status: ${batchData.status}
- Confidence: ${(batchData.confidence * 100).toFixed(1)}%
- SHAP Scores: ${JSON.stringify(batchData.features)}`;

  const contextPrompt = `Explain why this exact decision was made, using only the data payload above.`;

  // Map 'agent' → 'assistant' for OpenAI-compatible Groq API format
  const historyMessages = history.slice(-10).map(m => ({
    role: (m.role === 'agent' ? 'assistant' : 'user') as 'user' | 'assistant',
    content: m.text,
  }));

  try {
    const response = await fetch(GROQ_API_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${GROQ_API_KEY}`,
      },
      body: JSON.stringify({
        model: GROQ_MODEL,
        messages: [
          { role: 'system',    content: systemPrompt },
          { role: 'user',      content: contextPrompt },
          ...historyMessages,
          { role: 'user',      content: question },
        ],
        temperature: 0.3,
        max_tokens: 600,
      }),
    });

    if (!response.ok) {
      const errText = await response.text();
      throw new Error(`Groq API ${response.status}: ${errText}`);
    }

    const data = await response.json();
    const answer: string = data.choices?.[0]?.message?.content ?? 'No response received from agent.';

    return {
      answer,
      // LLM doesn't return calibrated confidence; use a fixed indicator
      confidence: 0.92,
      evidence_sources: [`GroqLLM/${GROQ_MODEL}`, `BatchData/${batchData.batch_id}`],
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Unknown error';
    return {
      answer: `❌ **Agent error:** ${msg}\n\nCheck your API key and network connection, then retry.`,
      confidence: 0,
      evidence_sources: [],
    };
  }
};

// ── Trace & chain endpoints (unchanged) ─────────────────────────────────────
export const getTrace = async (batchId: string): Promise<TraceEvent[]> => {
  await delay(800);
  return [
    { id: '1', timestamp: '2026-08-20T13:30:00Z', location: 'Mfg Facility Alpha', action: 'Manufactured', verified: true },
    { id: '2', timestamp: '2026-08-21T20:00:00Z', location: 'Logistics Hub 1',    action: 'In Transit',  verified: true },
    { id: '3', timestamp: '2026-08-23T14:45:00Z', location: 'Hospital Receiving', action: 'Arrived',     verified: true },
  ];
};

export const verifyChain = async (_batchId: string): Promise<boolean> => {
  await delay(1200);
  return true;
};
