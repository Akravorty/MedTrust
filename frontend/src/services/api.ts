import { BatchDecision, QAResponse, TraceEvent, Status } from '../types/schema';

// Mock delays
const delay = (ms: number) => new Promise(res => setTimeout(res, ms));

// Deterministic risk generator for dynamic batch payloads
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
    // Generate deterministic hash for custom input (e.g. BATCH-892)
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

  // Dynamic feature factors tailored to the batch
  let features = [
    { feature_name: 'temp_exc', display_label: 'Temperature excursion', value: status === 'REJECT' ? 0.78 : status === 'HOLD' ? 0.38 : 0.04 },
    { feature_name: 'supp_trend', display_label: 'Supplier trend deviation', value: status === 'REJECT' ? 0.62 : status === 'HOLD' ? 0.22 : 0.08 },
    { feature_name: 'viscosity_drift', display_label: 'Viscosity & pH drift', value: status === 'REJECT' ? 0.54 : status === 'HOLD' ? 0.16 : 0.02 },
    { feature_name: 'ocr_mismatch', display_label: 'OCR / Seal integrity', value: status === 'REJECT' ? 0.45 : status === 'HOLD' ? 0.11 : 0.01 },
  ];

  return {
    batch_id: batchId,
    status: status,
    confidence: Number(confidence.toFixed(2)),
    timestamp: new Date().toISOString(),
    features: features
  };
};

export const scanBatch = async (batchId: string, forceStatus?: Status): Promise<BatchDecision> => {
  await delay(600);
  return getBatchRiskData(batchId, forceStatus);
};

export const askAgent = async (
  batchId: string,
  question: string,
  history: Array<{ role: 'user' | 'agent'; text: string }> = [],
): Promise<QAResponse> => {
  await delay(900);

  const q = question.toLowerCase();
  const lastAgentMsg = [...history].reverse().find(m => m.role === 'agent')?.text ?? '';

  /* ── intent: scanner timeout / 15 seconds / blur / camera fail ── */
  if (/15|timeout|nothing|taking|not scan|blur|unreadable|camera/.test(q) && !/rejection|status|flag/.test(q)) {
    return {
      answer:
        `**Status:** SCAN TIMEOUT (15-Second Limit Exceeded)\n` +
        `**Alert:** The barcode/QR code is blurry or not properly visible.\n` +
        `**Action Required:** Please enter or provide the **Batch ID** of the product in the manual input box to proceed.`,
      confidence: 0.98,
      evidence_sources: ['ScannerTelemetry_Active', 'CameraTimeout_Alert'],
    };
  }

  /* ── intent: rejection / why / status / risk / confidence ── */
  if (/reject|why|status|flag|risk|confidence|score/.test(q)) {
    return {
      answer:
        `**Batch Status:** REJECTED (67.0% Confidence Score)\n\n` +
        `**Primary Risk Drivers (SHAP Impact):**\n` +
        `* **Temperature excursion (~0.78):** Major thermal breach logged during cold-chain transport between Logistics Hub 1 and Hospital Receiving.\n` +
        `* **Supplier trend deviation (~0.62):** Historical quality variance flagged for this vendor lot over the past 30-day window.\n` +
        `* **Viscosity & pH drift (~0.54):** Chemical stability metrics near USP tolerance thresholds.\n` +
        `* **OCR / Seal integrity (~0.45):** Secondary packaging barcode verification mismatch at receiving dock.\n\n` +
        `**Custody Timeline:** Manufactured at **Mfg Facility Alpha** (20 Aug, 13:30), transferred through **Logistics Hub 1** (21 Aug, 20:00), and flagged upon arrival at **Hospital Receiving** (23 Aug, 14:45).`,
      confidence: 0.97,
      evidence_sources: ['SensorLog_492', 'TransitManifest_A', 'SupplierDeviation_R44'],
    };
  }

  /* ── intent: what should I do / next actions / handle / recommend ── */
  if (/what.*do|action|next|handle|proceed|recommend|should/.test(q)) {
    return {
      answer:
        `**Recommended Next Actions:**\n\n` +
        `* **[Hold]:** Quarantine batch ${batchId} and request manual chromatography re-testing before any distribution decision.\n` +
        `* **[Reject]:** Log vendor quality deviation for thermal breach at Logistics Hub 1 and confirm batch disposal.\n` +
        `* **[Recall]:** Trigger emergency defect propagation if units from this lot have reached active hospital wards.\n` +
        `* **[Accept]:** Only if override authority approves a secondary-test clearance certificate.`,
      confidence: 0.93,
      evidence_sources: ['QA_SOP_v3.2', 'SupplierDeviation_R44'],
    };
  }

  /* ── intent: temperature / cold chain / thermal / excursion ── */
  if (/temp|cold.?chain|thermal|excurs/.test(q)) {
    return {
      answer:
        `**Temperature Excursion — SHAP Weight: ~0.78 (Primary Driver)**\n\n` +
        `* Excursion window: **21 Aug, 20:00 → 23 Aug, 14:45** (Logistics Hub 1 → Hospital Receiving).\n` +
        `* Duration: ~42 hours outside validated cold-chain range.\n` +
        `* Telemetry flagged in **SensorLog_492** and corroborated by **TransitManifest_A**.\n` +
        `* This single factor exceeds the 0.65 rejection threshold in QA ruleset v3.2.\n\n` +
        `**Live Stream:** BATCH-881 Cold-Chain Temp Alert is a concurrent peer incident on the audit stream.`,
      confidence: 0.98,
      evidence_sources: ['SensorLog_492', 'TransitManifest_A'],
    };
  }

  /* ── intent: supplier / vendor / deviation / trend ── */
  if (/supplier|vendor|deviation|trend/.test(q)) {
    return {
      answer:
        `**Supplier Trend Deviation — SHAP Weight: ~0.62**\n\n` +
        `* Vendor lot linked to 3 prior non-conformances in the past 30-day rolling window.\n` +
        `* Historical pass rate for this supplier is below the 85% acceptable threshold.\n` +
        `* Recommend escalating a **Corrective Action Preventive Action (CAPA)** report to the Procurement team.\n` +
        `* **[Reject]** action will auto-log the supplier deviation report in the quality ledger.`,
      confidence: 0.91,
      evidence_sources: ['SupplierDeviation_R44', 'VendorScorecard_Q3'],
    };
  }

  /* ── intent: viscosity / pH / drift / chemical / stability ── */
  if (/viscosity|ph|drift|chemical|stability/.test(q)) {
    return {
      answer:
        `**Viscosity & pH Drift — SHAP Weight: ~0.54**\n\n` +
        `* Chemical stability metrics flagged as near USP tolerance thresholds at receiving inspection.\n` +
        `* Drift is likely co-caused by the primary temperature excursion degrading the active pharmaceutical ingredient during the 42-hour transit breach.\n` +
        `* **[Hold]** action is recommended to allow manual spectrometry and chromatography re-inspection before a final decision.`,
      confidence: 0.88,
      evidence_sources: ['LabReport_V7', 'USP_Spec_Ref'],
    };
  }

  /* ── intent: seal / OCR / packaging / barcode / label ── */
  if (/seal|ocr|pack|barcode|integrity|label/.test(q)) {
    return {
      answer:
        `**OCR / Seal Integrity — SHAP Weight: ~0.45**\n\n` +
        `* Secondary packaging barcode verification returned a mismatch vs. manufacturer manifest at the receiving dock.\n` +
        `* Tamper-evident seal anomaly detected on secondary carton unit.\n` +
        `* Classified as a Level 2 packaging flag — does not independently trigger rejection but amplifies the composite risk score past the 0.65 threshold.`,
      confidence: 0.84,
      evidence_sources: ['PackagingAudit_D9', 'OCR_ScanLog_23Aug'],
    };
  }

  /* ── intent: audit / ledger / trace / custody / timeline / transit ── */
  if (/audit|ledger|trace|custody|timeline|history|transit|log/.test(q)) {
    return {
      answer:
        `**Custody Ledger — Batch ${batchId}:**\n\n` +
        `* **20 Aug, 13:30** — Manufactured at **Mfg Facility Alpha**. GMP-certified batch release. ✓\n` +
        `* **21 Aug, 20:00** — In Transit via **Logistics Hub 1**. Cold-chain handoff logged. ⚠ Thermal breach begins here.\n` +
        `* **23 Aug, 14:45** — Arrived at **Hospital Receiving**. Quality Gate scan triggered → REJECTED.\n\n` +
        `**Chain of Custody Integrity:** Verified (3/3 nodes confirmed on distributed ledger).`,
      confidence: 0.95,
      evidence_sources: ['LedgerNode_Alpha', 'LedgerNode_Hub1', 'LedgerNode_Hosp'],
    };
  }

  /* ── intent: live stream / other batches / today / metrics ── */
  if (/stream|live|today|metric|other|batch.?7|batch.?8|batch.?3|702|881|334|289/.test(q)) {
    return {
      answer:
        `**Live Audit Stream — Current Session:**\n\n` +
        `* BATCH-702: Spectrometry Pass ✓ — Released.\n` +
        `* BATCH-881: Cold-Chain Temp Alert ⚠ — Under review (peer thermal incident).\n` +
        `* BATCH-334: Chromatography Pass 99.4% ✓ — Released.\n` +
        `* BATCH-289: Released to ICU Ward B ✓.\n\n` +
        `**Session Metrics:** 142 batches scanned | **130 passed (91.5%)** | **12 flagged** (8 Hold, 4 Reject).`,
      confidence: 0.96,
      evidence_sources: ['AuditStream_Live', 'SessionLog_2026-08-25'],
    };
  }

  /* ── intent: follow-up / clarify / explain / elaborate / more ── */
  if (/mean|clarif|explain|elaborate|detail|more|what is|tell me|break/.test(q) || lastAgentMsg.length > 80) {
    const lastTopic = lastAgentMsg.includes('Temperature') ? 'Temperature Excursion'
      : lastAgentMsg.includes('Supplier') ? 'Supplier Trend Deviation'
      : lastAgentMsg.includes('Viscosity') ? 'Viscosity & pH Drift'
      : lastAgentMsg.includes('OCR') ? 'OCR / Seal Integrity'
      : lastAgentMsg.includes('Custody') || lastAgentMsg.includes('Ledger') ? 'Custody Ledger'
      : 'Composite Risk Profile';

    return {
      answer:
        `**Full Risk Factor Breakdown — ${lastTopic}:**\n\n` +
        `* **Temperature excursion (~0.78):** Major thermal breach logged during cold-chain transport.\n` +
        `* **Supplier trend deviation (~0.62):** Historical quality variance flagged for this vendor lot.\n` +
        `* **Viscosity & pH drift (~0.54):** Chemical stability metrics near tolerance thresholds.\n` +
        `* **OCR / Seal integrity (~0.45):** Secondary packaging visual verification flag.\n\n` +
        `**Composite Score:** 0.67 → Exceeds 0.65 rejection threshold → Decision: **REJECTED**.`,
      confidence: 0.92,
      evidence_sources: ['SensorLog_492', 'LabReport_V7', 'SupplierDeviation_R44'],
    };
  }

  /* ── default: batch summary + prompt ── */
  return {
    answer:
      `**Batch ${batchId} — Active QA Summary:**\n\n` +
      `* **Decision:** REJECTED (67.0% Confidence Score)\n` +
      `* **Top risk factor:** Temperature excursion (SHAP ~0.78) — thermal breach in transit leg.\n` +
      `* **Session:** 142 batches scanned | 130 passed (91.5%) | 12 flagged (8 Hold, 4 Reject)\n\n` +
      `Ask about: rejection reasons, recommended actions, temperature breach, supplier deviation, audit trail, seal integrity, or live stream.`,
    confidence: 0.89,
    evidence_sources: ['SensorLog_492', 'TransitManifest_A'],
  };
};

export const getTrace = async (batchId: string): Promise<TraceEvent[]> => {
  await delay(800);
  return [
    { id: '1', timestamp: '2026-08-20T13:30:00Z', location: 'Mfg Facility Alpha', action: 'Manufactured', verified: true },
    { id: '2', timestamp: '2026-08-21T20:00:00Z', location: 'Logistics Hub 1', action: 'In Transit', verified: true },
    { id: '3', timestamp: '2026-08-23T14:45:00Z', location: 'Hospital Receiving', action: 'Arrived', verified: true }
  ];
};

export const verifyChain = async (batchId: string): Promise<boolean> => {
  await delay(1200);
  return true;
};
