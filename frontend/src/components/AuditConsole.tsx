import React, { useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import { Search, Filter, Eye } from "lucide-react";
import type { AuditEventRow, DecisionOutcome, NormalizedPayment } from "../types/leash";

interface AuditConsoleProps {
  payments: NormalizedPayment[];
  auditEvents: AuditEventRow[];
  onSelectPayment: (payment: NormalizedPayment) => void;
  onSelectAuditEvent: (event: AuditEventRow) => void;
}

export const AuditConsole: React.FC<AuditConsoleProps> = ({
  payments,
  auditEvents,
  onSelectPayment,
  onSelectAuditEvent,
}) => {
  const [activeGrid, setActiveGrid] = useState<"payments" | "audit">("payments");
  const [quickFilter, setQuickFilter] = useState("");
  const [decisionFilter, setDecisionFilter] = useState<"ALL" | DecisionOutcome>("ALL");

  const filteredPayments = useMemo(() => {
    if (decisionFilter === "ALL") return payments;
    return payments.filter((p) => p.decision === decisionFilter);
  }, [payments, decisionFilter]);

  const paymentCols = useMemo<ColDef<NormalizedPayment>[]>(
    () => [
      {
        field: "created_at",
        headerName: "Timestamp",
        width: 185,
        sort: "desc",
        valueFormatter: (p) =>
          p.value ? new Date(String(p.value)).toLocaleString() : "—",
      },
      {
        field: "payment_id",
        headerName: "Payment ID",
        width: 150,
        cellClass: "font-mono text-xs text-slate-300",
      },
      {
        field: "merchant_id",
        headerName: "Merchant",
        width: 135,
        filter: true,
        cellRenderer: (p: ICellRendererParams<NormalizedPayment>) => {
          const m = p.value as string;
          const isMalicious = m?.toLowerCase().includes("megadeals");
          return (
            <span
              className={`font-semibold ${
                isMalicious ? "text-rose-300" : "text-slate-100"
              }`}
            >
              {m}
            </span>
          );
        },
      },
      {
        field: "amount",
        headerName: "Amount",
        width: 120,
        valueGetter: (p) => (p.data ? `${p.data.amount} ${p.data.currency}` : ""),
        cellClass: "font-mono font-semibold text-right",
      },
      {
        field: "decision",
        headerName: "Decision",
        width: 130,
        filter: true,
        cellRenderer: (p: ICellRendererParams<NormalizedPayment>) => {
          const val = p.value as DecisionOutcome;
          const badgeStyle =
            val === "ALLOW"
              ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
              : val === "STEP_UP"
                ? "bg-amber-500/20 text-amber-300 border-amber-500/40"
                : "bg-rose-500/20 text-rose-300 border-rose-500/40";
          return (
            <span
              className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-bold ${badgeStyle}`}
            >
              {val}
            </span>
          );
        },
      },
      {
        field: "status",
        headerName: "Status",
        width: 130,
        filter: true,
        cellClass: "font-mono text-xs text-slate-300",
      },
      {
        field: "risk_score",
        headerName: "XGBoost Risk",
        width: 145,
        sortable: true,
        cellRenderer: (p: ICellRendererParams<NormalizedPayment>) => {
          const score = p.value as number | null;
          if (score === null || score === undefined) {
            return <span className="text-slate-500">—</span>;
          }
          const color =
            score >= 0.7
              ? "text-rose-400"
              : score >= 0.35
                ? "text-amber-300"
                : "text-emerald-400";
          return <span className={`font-mono font-bold ${color}`}>{score.toFixed(3)}</span>;
        },
      },
      {
        headerName: "Intent Judge",
        width: 140,
        valueGetter: (p) => p.data?.judge?.intent_verdict ?? "—",
        cellClass: "font-mono text-xs",
      },
      {
        headerName: "Policy / Block Reasons",
        flex: 1,
        minWidth: 240,
        valueGetter: (p) =>
          p.data?.reasons && p.data.reasons.length > 0
            ? p.data.reasons.join(", ")
            : p.data?.judge?.explanation || "Mandate & policy verified",
      },
      {
        headerName: "Inspect",
        width: 100,
        pinned: "right",
        cellRenderer: (p: ICellRendererParams<NormalizedPayment>) => (
          <button
            type="button"
            onClick={() => p.data && onSelectPayment(p.data)}
            className="inline-flex items-center gap-1 rounded bg-slate-800 px-2 py-1 text-xs text-emerald-300 hover:bg-slate-700"
          >
            <Eye className="h-3 w-3" />
            View
          </button>
        ),
      },
    ],
    [onSelectPayment]
  );

  const auditCols = useMemo<ColDef<AuditEventRow>[]>(
    () => [
      {
        field: "id",
        headerName: "Event #",
        width: 100,
        sort: "desc",
        cellClass: "font-mono font-bold text-slate-200",
      },
      {
        field: "created_at",
        headerName: "Timestamp",
        width: 185,
        valueFormatter: (p) =>
          p.value ? new Date(String(p.value)).toLocaleString() : "—",
      },
      {
        field: "event_type",
        headerName: "Event Type",
        width: 190,
        filter: true,
        cellClass: "font-mono text-xs text-emerald-300",
      },
      {
        field: "payment_id",
        headerName: "Payment ID",
        width: 150,
        filter: true,
        cellClass: "font-mono text-xs text-slate-300",
      },
      {
        field: "prev_hash",
        headerName: "Prev Hash (SHA-256)",
        width: 190,
        valueFormatter: (p) =>
          p.value ? `${String(p.value).slice(0, 12)}…${String(p.value).slice(-6)}` : "—",
        cellClass: "font-mono text-xs text-slate-400",
      },
      {
        field: "hash",
        headerName: "Event Hash (SHA-256)",
        width: 190,
        valueFormatter: (p) =>
          p.value ? `${String(p.value).slice(0, 12)}…${String(p.value).slice(-6)}` : "—",
        cellClass: "font-mono text-xs text-emerald-400",
      },
      {
        headerName: "Payload Summary",
        flex: 1,
        minWidth: 240,
        valueGetter: (p) => (p.data ? JSON.stringify(p.data.payload) : ""),
        cellClass: "font-mono text-xs text-slate-300",
      },
      {
        headerName: "Inspect",
        width: 100,
        pinned: "right",
        cellRenderer: (p: ICellRendererParams<AuditEventRow>) => (
          <button
            type="button"
            onClick={() => p.data && onSelectAuditEvent(p.data)}
            className="inline-flex items-center gap-1 rounded bg-slate-800 px-2 py-1 text-xs text-emerald-300 hover:bg-slate-700"
          >
            <Eye className="h-3 w-3" />
            Hash
          </button>
        ),
      },
    ],
    [onSelectAuditEvent]
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/70 p-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setActiveGrid("payments")}
            className={`rounded-lg px-3.5 py-1.5 text-xs font-semibold transition ${
              activeGrid === "payments"
                ? "bg-emerald-500 text-slate-950"
                : "bg-slate-800 text-slate-300 hover:bg-slate-700"
            }`}
          >
            Payment Decisions ({filteredPayments.length})
          </button>
          <button
            type="button"
            onClick={() => setActiveGrid("audit")}
            className={`rounded-lg px-3.5 py-1.5 text-xs font-semibold transition ${
              activeGrid === "audit"
                ? "bg-emerald-500 text-slate-950"
                : "bg-slate-800 text-slate-300 hover:bg-slate-700"
            }`}
          >
            Tamper-Evident Audit Chain ({auditEvents.length})
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {activeGrid === "payments" && (
            <div className="flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-950 p-1">
              <Filter className="ml-1.5 h-3.5 w-3.5 text-slate-400" />
              {(["ALL", "ALLOW", "STEP_UP", "BLOCK"] as const).map((outcome) => (
                <button
                  key={outcome}
                  type="button"
                  onClick={() => setDecisionFilter(outcome)}
                  className={`rounded-md px-2.5 py-1 text-xs font-semibold transition ${
                    decisionFilter === outcome
                      ? "bg-slate-800 text-emerald-300"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {outcome}
                </button>
              ))}
            </div>
          )}

          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-400" />
            <input
              type="text"
              value={quickFilter}
              onChange={(e) => setQuickFilter(e.target.value)}
              placeholder="Search merchant, ID, reason, hash..."
              className="w-64 rounded-lg border border-slate-700 bg-slate-950 py-1.5 pl-8 pr-3 text-xs text-slate-100 placeholder-slate-500 focus:border-emerald-500 focus:outline-none"
            />
          </div>
        </div>
      </div>

      <div className="ag-theme-quartz-dark h-[540px] w-full overflow-hidden rounded-xl border border-slate-800 shadow-lg">
        {activeGrid === "payments" ? (
          <AgGridReact<NormalizedPayment>
            rowData={filteredPayments}
            columnDefs={paymentCols}
            quickFilterText={quickFilter}
            pagination={true}
            paginationPageSize={20}
            animateRows={true}
            onRowClicked={(e) => e.data && onSelectPayment(e.data)}
          />
        ) : (
          <AgGridReact<AuditEventRow>
            rowData={auditEvents}
            columnDefs={auditCols}
            quickFilterText={quickFilter}
            pagination={true}
            paginationPageSize={25}
            animateRows={true}
            onRowClicked={(e) => e.data && onSelectAuditEvent(e.data)}
          />
        )}
      </div>
    </div>
  );
};
