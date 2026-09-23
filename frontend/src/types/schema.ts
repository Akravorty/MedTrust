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