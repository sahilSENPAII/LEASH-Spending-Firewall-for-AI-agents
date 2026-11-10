import React from "react";
import { X, ShieldCheck, ShieldAlert, AlertTriangle, Hash } from "lucide-react";
import type { AuditEventRow, NormalizedPayment } from "../types/leash";

interface InspectorDrawerProps {
  payment: NormalizedPayment | null;
  auditEvent: AuditEventRow | null;
  onClose: () => void;
}

export const InspectorDrawer: React.FC<InspectorDrawerProps> = ({
  payment,
  auditEvent,
  onClose,
}) => {
  if (!payment && !auditEvent) return null;

  return (
    <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col border-l border-slate-800 bg-slate-950/95 shadow-2xl backdrop-blur-md">
      <div className="flex items-center justify-between border-b border-slate-800 px-5 py-4">
        <div className="flex items-center gap-2">
          <Hash className="h-4 w-4 text-emerald-400" />
          <h3 className="text-sm font-semibold text-slate-100">
            {payment
              ? `Payment Decision Inspector — ${payment.payment_id}`
              : `Audit Event Inspector — #${auditEvent?.id}`}
          </h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-5 text-xs">
        {payment && (
          <>
            <div className="grid grid-cols-2 gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
              <div>
                <p className="text-slate-400">Decision Outcome</p>
                <span
                  className={`mt-1 inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-bold ${
                    payment.decision === "ALLOW"
                      ? "bg-emerald-500/20 text-emerald-300"
                      : payment.decision === "STEP_UP"
                        ? "bg-amber-500/20 text-amber-300"
                        : "bg-rose-500/20 text-rose-300"
                  }`}
                >
                  {payment.decision === "ALLOW" && <ShieldCheck className="h-3.5 w-3.5" />}
                  {payment.decision === "STEP_UP" && <AlertTriangle className="h-3.5 w-3.5" />}
                  {payment.decision === "BLOCK" && <ShieldAlert className="h-3.5 w-3.5" />}
                  {payment.decision}
                </span>
              </div>
              <div>
                <p className="text-slate-400">Execution Status</p>
                <p className="mt-1 font-mono font-semibold text-slate-200">{payment.status}</p>
              </div>
              <div>
                <p className="text-slate-400">Merchant</p>
                <p className="mt-1 font-semibold text-slate-100">{payment.merchant_id}</p>
              </div>
              <div>
                <p className="text-slate-400">Amount</p>
                <p className="mt-1 font-mono text-sm font-bold text-slate-100">
                  {payment.amount} {payment.currency}
                </p>
              </div>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-slate-200">
                  XGBoost + SHAP Anomaly Score
                </span>
                <span className="font-mono font-bold text-slate-100">
                  {payment.risk_score !== null ? payment.risk_score.toFixed(4) : "N/A"}
                </span>
              </div>
              {payment.risk_score !== null && (
                <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-800">
                  <div
                    className={`h-full rounded-full ${
                      payment.risk_score >= 0.7
                        ? "bg-rose-500"
                        : payment.risk_score >= 0.35
                          ? "bg-amber-400"
                          : "bg-emerald-400"
                    }`}
                    style={{ width: `${Math.min(100, Math.round(payment.risk_score * 100))}%` }}
                  />
                </div>
              )}
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
              <p className="font-semibold text-slate-200">
                Policy & Mandate Evaluation Reasons
              </p>
              {payment.reasons.length === 0 ? (
                <p className="mt-1.5 text-emerald-400">
                  All deterministic mandate & policy checks passed.
                </p>
              ) : (
                <ul className="mt-2 list-inside list-disc space-y-1 text-rose-300">
                  {payment.reasons.map((r, i) => (
                    <li key={i} className="font-mono">
                      {r}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {payment.judge && (
              <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-slate-200">
                    Groq LLM Intent Judge (openai/gpt-oss-120b)
                  </span>
                  <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-slate-200">
                    {payment.judge.intent_verdict}
                  </span>
                </div>
                <p className="mt-2 leading-relaxed text-slate-300">
                  {payment.judge.explanation || "No additional judge commentary."}
                </p>
              </div>
            )}

            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
              <p className="font-semibold text-slate-200">Line Items</p>
              {payment.items.length === 0 ? (
                <p className="mt-1 text-slate-400">No line items recorded.</p>
              ) : (
                <div className="mt-2 divide-y divide-slate-800">
                  {payment.items.map((item, idx) => (
                    <div key={idx} className="flex items-center justify-between py-1.5">
                      <span className="text-slate-200">
                        {item.quantity}x {item.name}
                        {item.sku ? ` (${item.sku})` : ""}
                      </span>
                      <span className="font-mono text-slate-300">${item.unit_price}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        {auditEvent && (
          <>
            <div className="space-y-2 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
              <div className="flex justify-between">
                <span className="text-slate-400">Event ID</span>
                <span className="font-mono font-bold text-slate-100">#{auditEvent.id}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Event Type</span>
                <span className="font-mono text-emerald-300">{auditEvent.event_type}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Payment ID</span>
                <span className="font-mono text-slate-200">
                  {auditEvent.payment_id ?? "—"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Timestamp</span>
                <span className="font-mono text-slate-300">
                  {auditEvent.created_at ?? "—"}
                </span>
              </div>
            </div>

            <div className="space-y-2 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
              <p className="font-semibold text-slate-200">SHA-256 Hash Link</p>
              <div>
                <p className="text-[11px] text-slate-400">prev_hash</p>
                <p className="mt-0.5 break-all rounded bg-slate-950 p-2 font-mono text-[11px] text-slate-400">
                  {auditEvent.prev_hash}
                </p>
              </div>
              <div>
                <p className="text-[11px] text-slate-400">hash</p>
                <p className="mt-0.5 break-all rounded bg-slate-950 p-2 font-mono text-[11px] text-emerald-300">
                  {auditEvent.hash}
                </p>
              </div>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
              <p className="mb-2 font-semibold text-slate-200">Canonical Event Payload</p>
              <pre className="overflow-x-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] text-slate-300">
                {JSON.stringify(auditEvent.payload, null, 2)}
              </pre>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
