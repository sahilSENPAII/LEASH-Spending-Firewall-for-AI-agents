export type DecisionOutcome = "ALLOW" | "STEP_UP" | "BLOCK";

export type PaymentStatus =
  | "REQUESTED"
  | "AUTHORIZED"
  | "CAPTURED"
  | "VOIDED"
  | "BLOCKED"
  | "AWAITING_APPROVAL"
  | "DENIED"
  | "EXPIRED"
  | "FAILED"
  | string;

export interface PaymentItem {
  name: string;
  unit_price: string;
  quantity: number;
  sku?: string;
}

export interface JudgeVerdict {
  match?: boolean;
  score?: string;
  reason?: string;
  intent_verdict: DecisionOutcome | string;
  explanation: string;
}

export interface NormalizedPayment {
  payment_id: string;
  mandate_id: string | null;
  agent_id: string | null;
  merchant_id: string;
  amount: string;
  currency: string;
  decision: DecisionOutcome;
  status: PaymentStatus;
  reasons: string[];
  risk_score: number | null;
  risk_score_str: string | null;
  judge: JudgeVerdict | null;
  items: PaymentItem[];
  created_at: string | null;
  updated_at: string | null;
}

export interface AuditEventRow {
  id: number;
  created_at: string | null;
  event_type: string;
  payment_id: string | null;
  payload: Record<string, unknown>;
  prev_hash: string | null;
  hash: string | null;
}

export interface AuditVerifyResult {
  ok: boolean;
  checked: number;
  first_bad_id: number | null;
}

export interface HealthStatus {
  status: "ok" | "degraded" | "offline";
  db: "ok" | "down";
}
