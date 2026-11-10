import React, { useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import { Search } from "lucide-react";
import type { AuditEventRow, DecisionOutcome, NormalizedPayment } from "../types/leash";
import {
  DECISION_DOT,
  DECISION_LABEL,
  formatMoney,
  formatTime,
  humanize,
  shortHash,
} from "./format";

interface AuditConsoleProps {
  payments: NormalizedPayment[];
  auditEvents: AuditEventRow[];
  onSelectPayment: (payment: NormalizedPayment) => void;
  onSelectAuditEvent: (event: AuditEventRow) => void;
}

const FILTERS: readonly ("ALL" | DecisionOutcome)[] = ["ALL", "ALLOW", "STEP_UP", "BLOCK"];

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
        headerName: "Time",
        width: 160,
        sort: "desc",
        valueFormatter: (p) => formatTime(p.value as string | null),
        cellClass: "text-fg-2 tabular-nums",
      },
      {
        field: "payment_id",
        headerName: "Payment",
        width: 150,
        cellClass: "font-mono text-[12px] text-fg-2",
      },
      {
        field: "merchant_id",
        headerName: "Merchant",
        width: 130,
        filter: true,
      },
      {
        field: "amount",
        headerName: "Amount",
        width: 120,
        type: "rightAligned",
        comparator: (a, b) => Number(a) - Number(b),
        valueFormatter: (p) => (p.data ? formatMoney(p.data.amount, p.data.currency) : ""),
        cellClass: "font-mono tabular-nums",
      },
      {
        field: "decision",
        headerName: "Decision",
        width: 120,
        filter: true,
        cellRenderer: (p: ICellRendererParams<NormalizedPayment>) => {
          const val = p.value as DecisionOutcome;
          return (
            <span className="inline-flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${DECISION_DOT[val]}`} />
              {DECISION_LABEL[val] ?? val}
            </span>
          );
        },
      },
      {
        field: "status",
        headerName: "Status",
        width: 160,
        filter: true,
        valueFormatter: (p) => humanize(String(p.value ?? "").toLowerCase()),
        cellClass: "text-fg-2",
      },
      {
        field: "risk_score",
        headerName: "Risk",
        width: 120,
        cellRenderer: (p: ICellRendererParams<NormalizedPayment>) => {
          const score = p.value as number | null;
          if (score === null || score === undefined) {
            return <span className="text-fg-3">—</span>;
          }
          const tone = score >= 0.9 ? "text-block" : score >= 0.5 ? "text-stepup" : "text-fg-2";
          return <span className={`font-mono tabular-nums ${tone}`}>{score.toFixed(3)}</span>;
        },
      },
      {
        headerName: "Intent",
        width: 120,
        valueGetter: (p) => {
          const verdict = p.data?.judge?.intent_verdict;
          if (!verdict) return "—";
          if (verdict === "MATCH") return "Matches";
          if (verdict === "MISMATCH") return "Mismatch";
          return humanize(verdict.toLowerCase());
        },
        cellClass: "text-fg-2",
      },
      {
        headerName: "Reason",
        flex: 1,
        minWidth: 220,
        valueGetter: (p) =>
          p.data?.reasons && p.data.reasons.length > 0
            ? p.data.reasons.map(humanize).join(", ")
            : p.data?.judge?.explanation || "Within mandate",
        cellClass: "text-fg-2",
      },
    ],
    []
  );

  const auditCols = useMemo<ColDef<AuditEventRow>[]>(
    () => [
      {
        field: "id",
        headerName: "#",
        width: 80,
        sort: "desc",
        cellClass: "font-mono tabular-nums text-fg-2",
      },
      {
        field: "created_at",
        headerName: "Time",
        width: 160,
        valueFormatter: (p) => formatTime(p.value as string | null),
        cellClass: "text-fg-2 tabular-nums",
      },
      {
        field: "event_type",
        headerName: "Event",
        width: 180,
        filter: true,
        cellClass: "font-mono text-[12px]",
      },
      {
        field: "payment_id",
        headerName: "Payment",
        width: 150,
        filter: true,
        valueFormatter: (p) => (p.value as string | null) ?? "—",
        cellClass: "font-mono text-[12px] text-fg-2",
      },
      {
        field: "prev_hash",
        headerName: "Previous hash",
        width: 150,
        valueFormatter: (p) => shortHash(p.value as string | null),
        cellClass: "font-mono text-[12px] text-fg-3",
      },
      {
        field: "hash",
        headerName: "Hash",
        width: 150,
        valueFormatter: (p) => shortHash(p.value as string | null),
        cellClass: "font-mono text-[12px] text-fg",
      },
      {
        headerName: "Payload",
        flex: 1,
        minWidth: 220,
        valueGetter: (p) => (p.data ? JSON.stringify(p.data.payload) : ""),
        cellClass: "font-mono text-[12px] text-fg-3",
      },
    ],
    []
  );

  const tabClass = (active: boolean) =>
    `relative -mb-px inline-flex h-11 items-center gap-2 border-b-2 text-sm transition-colors duration-150 ${
      active ? "border-accent text-fg" : "border-transparent text-fg-3 hover:text-fg-2"
    }`;

  return (
    <section className="overflow-hidden rounded-2xl border border-line bg-card shadow-[0_8px_24px_rgba(12,43,42,0.08)]">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 border-b border-line px-5">
        <div className="flex items-center gap-6" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={activeGrid === "payments"}
            onClick={() => setActiveGrid("payments")}
            className={tabClass(activeGrid === "payments")}
          >
            Decisions
            <span className="font-mono text-[12px] text-fg-3">{filteredPayments.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeGrid === "audit"}
            onClick={() => setActiveGrid("audit")}
            className={tabClass(activeGrid === "audit")}
          >
            Audit chain
            <span className="font-mono text-[12px] text-fg-3">{auditEvents.length}</span>
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2 pb-2 sm:pb-0 sm:h-11">
          {activeGrid === "payments" && (
            <div className="flex h-8 items-center rounded-lg border border-line p-0.5">
              {FILTERS.map((outcome) => (
                <button
                  key={outcome}
                  type="button"
                  aria-pressed={decisionFilter === outcome}
                  onClick={() => setDecisionFilter(outcome)}
                  className={`h-full rounded-md px-2.5 text-[13px] transition-colors duration-150 ${
                    decisionFilter === outcome
                      ? "bg-raised text-fg "
                      : "text-fg-3 hover:text-fg-2"
                  }`}
                >
                  {outcome === "ALL" ? "All" : DECISION_LABEL[outcome]}
                </button>
              ))}
            </div>
          )}

          <label className="relative block">
            <span className="sr-only">Search</span>
            <Search
              className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-fg-3"
              strokeWidth={1.5}
            />
            <input
              type="search"
              value={quickFilter}
              onChange={(e) => setQuickFilter(e.target.value)}
              placeholder="Search"
              className="h-8 w-56 rounded-lg border border-line bg-sunken pl-8 pr-3 text-[13px] text-fg placeholder-fg-3 transition-colors duration-150 focus:border-accent focus:outline-none"
            />
          </label>
        </div>
      </div>

      <div className="ag-theme-quartz h-[560px] w-full">
        {activeGrid === "payments" ? (
          <AgGridReact<NormalizedPayment>
            key="payments"
            rowData={filteredPayments}
            columnDefs={paymentCols}
            quickFilterText={quickFilter}
            rowHeight={40}
            headerHeight={36}
            pagination={true}
            paginationPageSize={20}
            overlayNoRowsTemplate="No payments yet"
            onRowClicked={(e) => e.data && onSelectPayment(e.data)}
            onCellKeyDown={(e) => {
              if ((e.event as KeyboardEvent | null)?.key === "Enter" && e.data) onSelectPayment(e.data);
            }}
          />
        ) : (
          <AgGridReact<AuditEventRow>
            key="audit"
            rowData={auditEvents}
            columnDefs={auditCols}
            quickFilterText={quickFilter}
            rowHeight={40}
            headerHeight={36}
            pagination={true}
            paginationPageSize={20}
            overlayNoRowsTemplate="No audit events yet"
            onRowClicked={(e) => e.data && onSelectAuditEvent(e.data)}
            onCellKeyDown={(e) => {
              if ((e.event as KeyboardEvent | null)?.key === "Enter" && e.data) onSelectAuditEvent(e.data);
            }}
          />
        )}
      </div>
    </section>
  );
};
