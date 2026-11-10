import React from "react";
import { Link2, Link2Off, RefreshCw } from "lucide-react";
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
  let label: string;
  let tone: string;
  let Icon = Link2;
  if (verification === null) {
    label = "Chain not checked";
    tone = "text-fg-3";
  } else if (verification.ok) {
    label = `Chain intact · ${verification.checked} events`;
    tone = "text-fg-2";
  } else {
    label = `Chain broken at #${verification.first_bad_id ?? "?"}`;
    tone = "text-block";
    Icon = Link2Off;
  }

  return (
    <div className="flex items-center gap-2">
      <div
        className={`inline-flex h-8 items-center gap-2 rounded-lg border border-line px-3 text-[13px] ${tone}`}
        title="Result of GET /audit/verify, which recomputes every SHA-256 link"
      >
        <Icon className="h-4 w-4" strokeWidth={1.5} />
        <span className="tabular-nums">{label}</span>
      </div>

      <button
        type="button"
        onClick={onVerify}
        disabled={loading}
        className="inline-flex h-8 items-center gap-2 rounded-lg bg-fg px-3 text-[13px] font-medium text-canvas transition-[background-color,transform] duration-150 ease-out hover:bg-white active:scale-[0.98] disabled:opacity-60"
      >
        <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} strokeWidth={1.5} />
        Verify
      </button>
    </div>
  );
};
