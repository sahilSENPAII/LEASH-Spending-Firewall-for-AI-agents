import type { DecisionOutcome } from "../types/leash";

export const DECISION_LABEL: Record<DecisionOutcome, string> = {
  ALLOW: "Allow",
  STEP_UP: "Step-up",
  BLOCK: "Block",
};

export const DECISION_DOT: Record<DecisionOutcome, string> = {
  ALLOW: "bg-allow",
  STEP_UP: "bg-stepup",
  BLOCK: "bg-block",
};

export function formatMoney(amount: string, currency: string): string {
  const value = Number(amount);
  if (!Number.isFinite(value)) return `${amount} ${currency}`;
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(value);
  } catch {
    return `${value.toFixed(2)} ${currency}`;
  }
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function shortHash(value: string | null | undefined): string {
  if (!value) return "—";
  return value.length > 14 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

export function humanize(code: string): string {
  const text = code.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
