import type {
  AuditEventRow,
  AuditVerifyResult,
  DecisionOutcome,
  HealthStatus,
  JudgeVerdict,
  NormalizedPayment,
  PaymentItem,
} from "../types/leash";

const DEFAULT_API_URL = "http://localhost:8000";

export function getApiBaseUrl(): string {
  const envUrl = import.meta.env.VITE_API_URL as string | undefined;
  return (envUrl && envUrl.trim().replace(/\/+$/, "")) || DEFAULT_API_URL;
}

/**
 * Normalizes raw payment JSON from Leash's `backend/app/read_api.py` into a strict
 * NormalizedPayment shape. Supports top-level PaymentView fields, nested `decision`
 * audit payloads, object-shaped reasons, string-encoded risk scores, and JSON-stringified items.
 */
export function normalizePayment(raw: Record<string, unknown>): NormalizedPayment {
  const nestedDecision =
    typeof raw.decision === "object" && raw.decision !== null
      ? (raw.decision as Record<string, unknown>)
      : null;

  const rawOutcome =
    typeof raw.decision === "string"
      ? raw.decision
      : typeof nestedDecision?.decision === "string"
        ? nestedDecision.decision
        : typeof nestedDecision?.outcome === "string"
          ? nestedDecision.outcome
          : "BLOCK";

  const decision: DecisionOutcome =
    rawOutcome === "ALLOW" || rawOutcome === "STEP_UP" || rawOutcome === "BLOCK"
      ? rawOutcome
      : "BLOCK";

  const status =
    (typeof raw.status === "string" && raw.status) ||
    (typeof nestedDecision?.status === "string" && nestedDecision.status) ||
    (decision === "ALLOW" ? "CAPTURED" : decision === "STEP_UP" ? "AWAITING_APPROVAL" : "BLOCKED");

  // Reasons may be strings or objects {code, message, source}
  const rawReasons = Array.isArray(raw.reasons)
    ? raw.reasons
    : Array.isArray(nestedDecision?.reasons)
      ? nestedDecision.reasons
      : [];
  const reasons: string[] = rawReasons.map((r) => {
    if (typeof r === "string") return r;
    if (typeof r === "object" && r !== null) {
      const rec = r as Record<string, unknown>;
      return String(rec.code || rec.message || JSON.stringify(rec));
    }
    return String(r);
  });

  // Risk score can be string e.g. "0.942100", number e.g. 0.942, or null
  const rawRisk =
    raw.risk_score !== undefined
      ? raw.risk_score
      : nestedDecision?.risk_score;
  let riskNum: number | null = null;
  let riskStr: string | null = null;
  if (typeof rawRisk === "number") {
    riskNum = Number.isFinite(rawRisk) ? rawRisk : null;
    riskStr = riskNum !== null ? riskNum.toFixed(4) : null;
  } else if (typeof rawRisk === "string" && rawRisk.trim()) {
    const parsed = parseFloat(rawRisk);
    riskNum = Number.isFinite(parsed) ? parsed : null;
    riskStr = rawRisk;
  }

  // Judge can be {match, score, reason} or {intent_verdict, explanation}
  const rawJudge =
    typeof raw.judge === "object" && raw.judge !== null
      ? (raw.judge as Record<string, unknown>)
      : typeof nestedDecision?.judge === "object" && nestedDecision.judge !== null
        ? (nestedDecision.judge as Record<string, unknown>)
        : null;

  let judge: JudgeVerdict | null = null;
  if (rawJudge) {
    const match = typeof rawJudge.match === "boolean" ? rawJudge.match : undefined;
    const score = rawJudge.score !== undefined ? String(rawJudge.score) : undefined;
    const reasonText = String(rawJudge.explanation ?? rawJudge.reason ?? "");
    let intentVerdict = String(rawJudge.intent_verdict ?? rawJudge.verdict ?? "");
    if (!intentVerdict) {
      if (match === true) intentVerdict = "MATCH (ALLOW)";
      else if (match === false) intentVerdict = "MISMATCH (BLOCK)";
      else intentVerdict = decision;
    }
    judge = {
      match,
      score,
      reason: reasonText,
      intent_verdict: intentVerdict,
      explanation: reasonText,
    };
  }

  // Items can be a JSON string like '[{"sku": "sh-sony-xm5", "quantity": 1}]' or an array
  let parsedItemsRaw: unknown[] = [];
  if (Array.isArray(raw.items)) {
    parsedItemsRaw = raw.items;
  } else if (typeof raw.items === "string" && raw.items.trim()) {
    try {
      const parsed = JSON.parse(raw.items);
      if (Array.isArray(parsed)) parsedItemsRaw = parsed;
    } catch {
      // not json, leave empty
    }
  }

  const items: PaymentItem[] = parsedItemsRaw.map((item) => {
    const obj = (typeof item === "object" && item !== null ? item : {}) as Record<string, unknown>;
    const sku = obj.sku ? String(obj.sku) : undefined;
    const name = String(obj.name ?? sku ?? "Item");
    const unitPrice = String(obj.unit_price ?? obj.price ?? "0.00");
    const quantity = Number(obj.quantity ?? 1);
    return { name, unit_price: unitPrice, quantity, sku };
  });

  return {
    payment_id: String(raw.payment_id ?? raw.id ?? ""),
    mandate_id: raw.mandate_id ? String(raw.mandate_id) : null,
    agent_id: raw.agent_id ? String(raw.agent_id) : null,
    merchant_id: String(raw.merchant_id ?? "unknown"),
    amount: String(raw.amount ?? "0.00"),
    currency: String(raw.currency ?? "USD"),
    decision,
    status,
    reasons,
    risk_score: riskNum,
    risk_score_str: riskStr,
    judge,
    items,
    created_at: raw.created_at ? String(raw.created_at) : null,
    updated_at: raw.updated_at ? String(raw.updated_at) : null,
  };
}

async function fetchWithFallback<T>(
  baseUrl: string,
  primaryPath: string,
  fallbackPath?: string
): Promise<T> {
  const res = await fetch(`${baseUrl}${primaryPath}`, {
    headers: { Accept: "application/json" },
  });
  if (res.ok) {
    return (await res.json()) as T;
  }
  if (res.status === 404 && fallbackPath) {
    const fallbackRes = await fetch(`${baseUrl}${fallbackPath}`, {
      headers: { Accept: "application/json" },
    });
    if (fallbackRes.ok) {
      return (await fallbackRes.json()) as T;
    }
  }
  throw new Error(`HTTP ${res.status} on ${primaryPath}`);
}

export async function fetchPayments(
  limit = 100,
  baseUrl = getApiBaseUrl()
): Promise<NormalizedPayment[]> {
  const rawList = await fetchWithFallback<Record<string, unknown>[]>(
    baseUrl,
    `/payments?limit=${limit}`,
    `/api/v1/payments?limit=${limit}`
  );
  return rawList.map(normalizePayment);
}

export async function fetchPaymentById(
  paymentId: string,
  baseUrl = getApiBaseUrl()
): Promise<NormalizedPayment> {
  const raw = await fetchWithFallback<Record<string, unknown>>(
    baseUrl,
    `/payments/${encodeURIComponent(paymentId)}`,
    `/api/v1/payments/${encodeURIComponent(paymentId)}`
  );
  return normalizePayment(raw);
}

export async function fetchAuditLog(
  afterId = 0,
  limit = 200,
  baseUrl = getApiBaseUrl()
): Promise<AuditEventRow[]> {
  return fetchWithFallback<AuditEventRow[]>(
    baseUrl,
    `/audit?after_id=${afterId}&limit=${limit}`,
    `/api/v1/audit?after_id=${afterId}&limit=${limit}`
  );
}

export async function verifyAuditChain(
  baseUrl = getApiBaseUrl()
): Promise<AuditVerifyResult> {
  return fetchWithFallback<AuditVerifyResult>(
    baseUrl,
    `/audit/verify`,
    `/api/v1/audit/verify`
  );
}

export async function fetchHealth(
  baseUrl = getApiBaseUrl()
): Promise<HealthStatus> {
  try {
    const res = await fetch(`${baseUrl}/health`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return { status: "degraded", db: "down" };
    return (await res.json()) as HealthStatus;
  } catch {
    return { status: "offline", db: "down" };
  }
}
