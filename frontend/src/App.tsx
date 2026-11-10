import React, { useCallback, useEffect, useState } from "react";
import { Shield, Activity, Terminal } from "lucide-react";
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
  HealthStatus,
  NormalizedPayment,
} from "./types/leash";
import { AuditConsole } from "./components/AuditConsole";
import { ChainVerifyBadge } from "./components/ChainVerifyBadge";
import { InspectorDrawer } from "./components/InspectorDrawer";

export function App(): React.ReactElement {
  const [payments, setPayments] = useState<NormalizedPayment[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEventRow[]>([]);
  const [verification, setVerification] = useState<AuditVerifyResult | null>(null);
  const [health, setHealth] = useState<HealthStatus>({ status: "offline", db: "down" });
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
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
    } catch (err) {
      setError(
        err instanceof Error
          ? `${err.message} — Ensure FastAPI backend is running on ${getApiBaseUrl()}`
          : "Failed to connect to LEASH backend"
      );
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

  const allowCount = payments.filter((p) => p.decision === "ALLOW").length;
  const stepUpCount = payments.filter((p) => p.decision === "STEP_UP").length;
  const blockCount = payments.filter((p) => p.decision === "BLOCK").length;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-500/15 border border-emerald-500/40">
              <Shield className="h-5 w-5 text-emerald-400" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold tracking-tight text-white">
                  LEASH Firewall Console
                </h1>
                <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-[11px] text-emerald-300">
                  MCP + PayPal v2
                </span>
              </div>
              <p className="text-xs text-slate-400">
                Ed25519 Mandates • Deterministic Policy • Groq Intent Judge • XGBoost/SHAP • AG Grid Audit
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1.5 rounded-full border border-slate-800 bg-slate-950 px-3 py-1 text-xs">
              <Activity
                className={`h-3.5 w-3.5 ${
                  health.status === "ok" ? "text-emerald-400" : "text-amber-400"
                }`}
              />
              <span className="text-slate-400">API:</span>
              <span className="font-mono text-slate-200">{getApiBaseUrl()}</span>
            </div>

            <ChainVerifyBadge
              verification={verification}
              loading={loading}
              onVerify={() => void loadData(true)}
            />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
        {error && (
          <div className="flex items-center justify-between rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-xs text-amber-200">
            <div className="flex items-center gap-2">
              <Terminal className="h-4 w-4 text-amber-400" />
              <span>{error}</span>
            </div>
            <button
              type="button"
              onClick={() => void loadData(true)}
              className="rounded bg-amber-500/20 px-2.5 py-1 font-semibold text-amber-200 hover:bg-amber-500/30"
            >
              Retry Now
            </button>
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <p className="text-xs text-slate-400">Total Evaluated Payments</p>
            <p className="mt-1 font-mono text-2xl font-bold text-white">{payments.length}</p>
          </div>
          <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4">
            <p className="text-xs text-emerald-300">ALLOW (Auto-approved)</p>
            <p className="mt-1 font-mono text-2xl font-bold text-emerald-400">{allowCount}</p>
          </div>
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
            <p className="text-xs text-amber-300">STEP_UP (Human Approval)</p>
            <p className="mt-1 font-mono text-2xl font-bold text-amber-400">{stepUpCount}</p>
          </div>
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-4">
            <p className="text-xs text-rose-300">BLOCK (Firewall Stopped)</p>
            <p className="mt-1 font-mono text-2xl font-bold text-rose-400">{blockCount}</p>
          </div>
        </div>

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
      </main>

      <InspectorDrawer
        payment={selectedPayment}
        auditEvent={selectedAudit}
        onClose={() => {
          setSelectedPayment(null);
          setSelectedAudit(null);
        }}
      />
    </div>
  );
}

export default App;
