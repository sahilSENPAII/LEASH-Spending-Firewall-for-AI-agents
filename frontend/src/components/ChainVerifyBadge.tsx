import React from "react";
import { ShieldCheck, ShieldAlert, RefreshCw } from "lucide-react";
import type { AuditVerifyResult } from "../types/leash";

interface ChainVerifyBadgeProps {
  verification: AuditVerifyResult | null;
  loading: boolean;
  onVerify: () => void;
}

export const ChainVerifyBadge: React.FC<ChainVerifyBadgeProps> = ({
  verification,
  loading,
  onVerify,
}) => {
  const isOk = verification?.ok ?? true;
  const checked = verification?.checked ?? 0;
  const badId = verification?.first_bad_id;

  return (
    <div className="flex items-center gap-2">
      <div
        className={`inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-xs font-semibold transition-colors ${
          isOk
            ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
            : "border-rose-500/50 bg-rose-500/15 text-rose-300"
        }`}
        title="Cryptographic SHA-256 hash chain status from GET /audit/verify"
      >
        {isOk ? (
          <ShieldCheck className="h-4 w-4 text-emerald-400" />
        ) : (
          <ShieldAlert className="h-4 w-4 text-rose-400" />
        )}
        <span>
          {isOk
            ? `SHA-256 Chain Verified (${checked} events)`
            : `Tamper Detected at Event #${badId ?? "?"}`}
        </span>
      </div>

      <button
        type="button"
        onClick={onVerify}
        disabled={loading}
        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800/80 px-2.5 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50"
      >
        <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
        Re-verify Chain
      </button>
    </div>
  );
};
