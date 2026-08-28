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
}

export interface QAResponse {
   answer: string;
-  confidence: number;
+  confidence: 'HIGH' | 'MEDIUM' | 'INSUFFICIENT_EVIDENCE';
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
