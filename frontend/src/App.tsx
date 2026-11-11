import React, { useCallback, useEffect, useState } from "react";
import { RotateCw } from "lucide-react";
import {
  fetchAllAuditLog,
  fetchHealth,
  fetchPayments,
  getApiBaseUrl,
  verifyAuditChain,
} from "./api/readApi";
import type {
  AuditEventRow,
  AuditVerifyResult,
  DecisionOutcome,
  HealthStatus,
  NormalizedPayment,
} from "./types/leash";
import { AuditConsole } from "./components/AuditConsole";
import { ChainVerifyBadge } from "./components/ChainVerifyBadge";
import { InspectorDrawer } from "./components/InspectorDrawer";
import { DECISION_LABEL } from "./components/format";

const OUTCOMES: DecisionOutcome[] = ["ALLOW", "STEP_UP", "BLOCK"];

// Brighter steps of the decision colours, for the dark band.
const BAND_DOT: Record<DecisionOutcome, string> = {
  ALLOW: "bg-[#4fd1c1]",
  STEP_UP: "bg-[#f7c66b]",
  BLOCK: "bg-[#ff8a7a]",
};

function apiHost(): string {
  try {
    return new URL(getApiBaseUrl()).host;
  } catch {
    return getApiBaseUrl();
  }
}

function share(count: number, total: number): string {
  return total === 0 ? "—" : `${Math.round((count / total) * 100)}% of total`;
}

export function App(): React.ReactElement {
  const [payments, setPayments] = useState<NormalizedPayment[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEventRow[]>([]);
  const [verification, setVerification] = useState<AuditVerifyResult | null>(null);
  const [health, setHealth] = useState<HealthStatus>({ status: "offline", db: "down" });
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [selectedPayment, setSelectedPayment] = useState<NormalizedPayment | null>(null);
  const [selectedAudit, setSelectedAudit] = useState<AuditEventRow | null>(null);

  // Only a manual refresh shows the spinner; the 5 s poll updates quietly.
  const loadData = useCallback(async (manual = false) => {
    if (manual) setLoading(true);
    setError(null);
    try {
      const [paymentsData, auditData, verifyData, healthData] = await Promise.all([
        fetchPayments(100),
        fetchAllAuditLog(),
        verifyAuditChain(),
        fetchHealth(),
      ]);
      setPayments(paymentsData);
      setAuditEvents(auditData);
      setVerification(verifyData);
      setHealth(healthData);
      setUpdatedAt(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      if (manual) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData(true);
    const timer = window.setInterval(() => {
      void loadData();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [loadData]);

  const closeInspector = useCallback(() => {
    setSelectedPayment(null);
    setSelectedAudit(null);
  }, []);

  const total = payments.length;
  const counts: Record<DecisionOutcome, number> = { ALLOW: 0, STEP_UP: 0, BLOCK: 0 };
  for (const p of payments) counts[p.decision] += 1;

  const online = !error && health.status === "ok";
  const statusDot = error
    ? "bg-[#ff9c8f]"
    : health.status === "ok"
      ? "bg-[#6ee7d8]"
      : health.status === "degraded"
        ? "bg-[#f7c66b]"
        : "bg-white/40";

  return (
    <div className="min-h-screen bg-canvas font-sans text-fg">
      <header className="sticky top-0 z-30 border-b border-white/10 bg-band text-white">
        <div className="mx-auto flex h-14 max-w-[1440px] items-center justify-between gap-4 px-4 sm:px-8">
          <div className="flex items-center gap-3">
            <svg
              viewBox="0 0 64 64"
              className="h-6 w-6"
              fill="none"
              stroke="currentColor"
              strokeWidth="6"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M15 10V38a8 8 0 0 0 8 8H33" />
              <circle cx="41" cy="46" r="8" />
            </svg>
            <span className="text-[17px] font-semibold tracking-tight">Leash</span>
            <span className="hidden text-sm text-white/60 sm:inline">Audit console</span>
          </div>

          <div
            className="hidden items-center gap-2 text-[13px] text-white/90 md:flex"
            title={getApiBaseUrl()}
          >
            <span className={`h-2 w-2 rounded-full ${statusDot}`} />
            <span className="font-mono text-xs">{apiHost()}</span>
            {updatedAt && (
              <span className="text-white/60 tabular-nums">
                · updated{" "}
                {updatedAt.toLocaleTimeString("en-US", { hour12: false })}
              </span>
            )}
          </div>
        </div>
      </header>

      <div className="bg-band pb-24 text-white">
        <div className="mx-auto max-w-[1440px] px-4 pt-8 sm:px-8">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <h1 className="text-[28px] font-medium leading-tight tracking-tight">
                Payment decisions
              </h1>
              <p className="mt-1 text-sm text-white/70">
                Every payment an agent requested, checked against its signed mandate
              </p>
            </div>
            <ChainVerifyBadge
              verification={verification}
              loading={loading}
              onVerify={() => void loadData(true)}
            />
          </div>

          {error && (
            <div
              role="alert"
              className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#ff8a7a]/40 bg-white/[0.06] px-4 py-3"
            >
              <div className="min-w-0 text-sm">
                <p className="text-white">Can't reach the Leash API at {getApiBaseUrl()}</p>
                <p className="mt-0.5 font-mono text-xs text-white/60">{error}</p>
              </div>
              <button
                type="button"
                onClick={() => void loadData(true)}
                className="inline-flex h-8 items-center gap-2 rounded-lg border border-white/15 bg-white/10 px-3 text-[13px] text-white transition-colors duration-150 hover:bg-white/[0.16]"
              >
                <RotateCw className="h-4 w-4" strokeWidth={1.5} />
                Retry
              </button>
            </div>
          )}

          <div className="mt-6 grid grid-cols-3 gap-3 sm:gap-4 lg:grid-cols-5">
            <div className="col-span-3 rounded-2xl border border-white/10 bg-white/[0.06] p-5 lg:col-span-2">
              <div className="flex items-baseline justify-between">
                <p className="text-[13px] text-white/70">Payments checked</p>
                <p className="text-xs text-white/60">{online ? "Latest 100" : "No data"}</p>
              </div>
              <p className="mt-3 font-mono text-[32px] leading-none tabular-nums text-[#6ee7d8]">{total}</p>
              <div
                className="mt-5 flex h-2 gap-0.5 overflow-hidden rounded-full bg-black/25"
                role="img"
                aria-label={OUTCOMES.map((o) => `${DECISION_LABEL[o]} ${counts[o]}`).join(", ")}
              >
                {total > 0 &&
                  OUTCOMES.filter((o) => counts[o] > 0).map((o) => (
                    <div
                      key={o}
                      className={BAND_DOT[o]}
                      style={{ width: `${(counts[o] / total) * 100}%` }}
                    />
                  ))}
              </div>
              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-white/70">
                {OUTCOMES.map((o) => (
                  <span key={o} className="inline-flex items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${BAND_DOT[o]}`} />
                    {DECISION_LABEL[o]}
                  </span>
                ))}
              </div>
            </div>

            {OUTCOMES.map((o) => (
              <div key={o} className="rounded-2xl border border-white/10 bg-white/[0.06] p-4 sm:p-5">
                <p className="flex items-center gap-2 text-[13px] text-white/70">
                  <span className={`h-2 w-2 rounded-full ${BAND_DOT[o]}`} />
                  {o === "ALLOW" ? "Allowed" : o === "STEP_UP" ? "Step-up" : "Blocked"}
                </p>
                <p className="mt-3 font-mono text-[28px] leading-none tabular-nums sm:text-[32px]">
                  {counts[o]}
                </p>
                <p className="mt-3 text-xs text-white/60 tabular-nums">{share(counts[o], total)}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      <main className="mx-auto max-w-[1440px] px-4 pb-16 sm:px-8">
        <div className="-mt-16">
          <AuditConsole
            payments={payments}
            auditEvents={auditEvents}
            onSelectPayment={(p) => {
              setSelectedAudit(null);
              setSelectedPayment(p);
            }}
            onSelectAuditEvent={(ev) => {
              setSelectedPayment(null);
              setSelectedAudit(ev);
            }}
          />
        </div>
      </main>

      <InspectorDrawer
        payment={selectedPayment}
        auditEvent={selectedAudit}
        onClose={closeInspector}
      />
    </div>
  );
}

export default App;
