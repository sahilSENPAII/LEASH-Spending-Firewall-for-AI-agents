import React, { useEffect } from "react";
import { X } from "lucide-react";
import type { AuditEventRow, NormalizedPayment } from "../types/leash";
import { DECISION_DOT, DECISION_LABEL, formatMoney, formatTime, humanize } from "./format";

interface InspectorDrawerProps {
  payment: NormalizedPayment | null;
  auditEvent: AuditEventRow | null;
  onClose: () => void;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-line px-6 py-5">
      <h4 className="mb-3 text-[13px] text-fg-3">{title}</h4>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-fg-3">{label}</dt>
      <dd className="mt-1 truncate text-sm text-fg">{children}</dd>
    </div>
  );
}

// Thresholds match the backend's anomaly wiring: STEP_UP from 0.5, BLOCK from 0.9.
function RiskMeter({ score }: { score: number }) {
  const pct = Math.min(100, Math.max(0, score * 100));
  const tone = score >= 0.9 ? "bg-block" : score >= 0.5 ? "bg-stepup" : "bg-fg-2";
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-2xl tabular-nums text-fg">{score.toFixed(3)}</span>
        <span className="text-xs text-fg-3">XGBoost anomaly score</span>
      </div>
      <div className="relative mt-3 h-1.5 rounded-full bg-raised">
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${pct}%` }} />
        <span className="absolute -top-1 h-3.5 w-px bg-fg-3" style={{ left: "50%" }} />
        <span className="absolute -top-1 h-3.5 w-px bg-fg-3" style={{ left: "90%" }} />
      </div>
      <div className="relative mt-2 h-4 font-mono text-[11px] text-fg-3">
        <span className="absolute left-0">0</span>
        <span className="absolute -translate-x-1/2" style={{ left: "50%" }}>
          step-up 0.5
        </span>
        <span className="absolute -translate-x-1/2" style={{ left: "90%" }}>
          block 0.9
        </span>
      </div>
    </div>
  );
}

function HashBlock({ label, value, current }: { label: string; value: string | null; current?: boolean }) {
  return (
    <div className={`rounded-lg border p-3 ${current ? "border-line-strong bg-raised" : "border-line bg-sunken"}`}>
      <p className="text-xs text-fg-3">{label}</p>
      <p className={`mt-1 break-all font-mono text-xs leading-5 ${current ? "text-fg" : "text-fg-2"}`}>
        {value ?? "—"}
      </p>
    </div>
  );
}

export const InspectorDrawer: React.FC<InspectorDrawerProps> = ({
  payment,
  auditEvent,
  onClose,
}) => {
  const open = Boolean(payment || auditEvent);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} aria-hidden="true" />
      <aside
        role="dialog"
        aria-label={payment ? `Payment ${payment.payment_id}` : `Audit event ${auditEvent?.id}`}
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[480px] flex-col border-l border-line bg-card"
      >
        <header className="flex h-16 shrink-0 items-center justify-between px-6">
          <div className="min-w-0">
            <p className="text-xs text-fg-3">{payment ? "Payment" : "Audit event"}</p>
            <h3 className="truncate font-mono text-sm text-fg">
              {payment ? payment.payment_id : `#${auditEvent?.id}`}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-fg-3 transition-colors duration-150 hover:bg-raised hover:text-fg"
          >
            <X className="h-4 w-4" strokeWidth={1.5} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto pb-6">
          {payment && (
            <>
              <section className="border-t border-line px-6 py-5">
                <div className="flex items-center gap-2 text-sm text-fg">
                  <span className={`h-2 w-2 rounded-full ${DECISION_DOT[payment.decision]}`} />
                  {DECISION_LABEL[payment.decision]}
                  <span className="text-fg-3">·</span>
                  <span className="text-fg-2">{humanize(payment.status.toLowerCase())}</span>
                </div>
                <p className="mt-3 font-mono text-[32px] leading-none tabular-nums text-fg">
                  {formatMoney(payment.amount, payment.currency)}
                </p>
                <dl className="mt-5 grid grid-cols-2 gap-4">
                  <Field label="Merchant">{payment.merchant_id}</Field>
                  <Field label="Mandate">
                    <span className="font-mono text-[13px]">{payment.mandate_id ?? "—"}</span>
                  </Field>
                  <Field label="Agent">
                    <span className="font-mono text-[13px]">{payment.agent_id ?? "—"}</span>
                  </Field>
                  <Field label="Requested">
                    <span className="tabular-nums">{formatTime(payment.created_at)}</span>
                  </Field>
                </dl>
              </section>

              <Section title="Policy and mandate">
                {payment.reasons.length === 0 ? (
                  <p className="text-sm text-fg-2">Every mandate and policy check passed</p>
                ) : (
                  <ul className="space-y-2">
                    {payment.reasons.map((r, i) => (
                      <li key={i} className="flex items-start gap-3 text-sm">
                        <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-block" />
                        <span className="text-fg">
                          {humanize(r)}
                          <span className="ml-2 font-mono text-xs text-fg-3">{r}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </Section>

              {payment.risk_score !== null && (
                <Section title="Anomaly model">
                  <RiskMeter score={payment.risk_score} />
                </Section>
              )}

              {payment.judge && (
                <Section title="Intent judge">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-fg">
                      {payment.judge.intent_verdict === "MATCH"
                        ? "Matches the mandate's intent"
                        : payment.judge.intent_verdict === "MISMATCH"
                          ? "Does not match the mandate's intent"
                          : humanize(payment.judge.intent_verdict.toLowerCase())}
                    </span>
                    {payment.judge.score && (
                      <span className="font-mono text-xs tabular-nums text-fg-3">
                        {Number(payment.judge.score).toFixed(2)}
                      </span>
                    )}
                  </div>
                  {payment.judge.explanation && (
                    <p className="mt-2 text-sm leading-6 text-fg-2">{payment.judge.explanation}</p>
                  )}
                </Section>
              )}

              <Section title="Line items">
                {payment.items.length === 0 ? (
                  <p className="text-sm text-fg-3">No line items recorded</p>
                ) : (
                  <ul className="divide-y divide-line">
                    {payment.items.map((item, idx) => (
                      <li key={idx} className="flex items-center justify-between gap-4 py-2 text-sm">
                        <span className="min-w-0">
                          <span className="text-fg">
                            {item.quantity} × {item.name}
                          </span>
                          {item.sku && item.sku !== item.name && (
                            <span className="ml-2 font-mono text-xs text-fg-3">{item.sku}</span>
                          )}
                        </span>
                        <span className="font-mono tabular-nums text-fg-2">
                          {formatMoney(item.unit_price, payment.currency)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </Section>
            </>
          )}

          {auditEvent && (
            <>
              <section className="border-t border-line px-6 py-5">
                <p className="font-mono text-sm text-fg">{auditEvent.event_type}</p>
                <dl className="mt-4 grid grid-cols-2 gap-4">
                  <Field label="Payment">
                    <span className="font-mono text-[13px]">{auditEvent.payment_id ?? "—"}</span>
                  </Field>
                  <Field label="Recorded">
                    <span className="tabular-nums">{formatTime(auditEvent.created_at)}</span>
                  </Field>
                </dl>
              </section>

              <Section title="Hash link">
                <HashBlock label="Previous hash" value={auditEvent.prev_hash} />
                <div className="ml-6 h-4 w-px bg-line-strong" />
                <HashBlock label={`Hash of event #${auditEvent.id}`} value={auditEvent.hash} current />
                <p className="mt-3 text-xs leading-5 text-fg-3">
                  SHA-256 over the previous hash and this event's canonical JSON
                </p>
              </Section>

              <Section title="Payload">
                <pre className="overflow-x-auto rounded-lg border border-line bg-sunken p-3 font-mono text-xs leading-5 text-fg-2">
                  {JSON.stringify(auditEvent.payload, null, 2)}
                </pre>
              </Section>
            </>
          )}
        </div>
      </aside>
    </>
  );
};
