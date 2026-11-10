import { describe, expect, it } from "vitest";
import { normalizePayment } from "../api/readApi";

describe("normalizePayment", () => {
  it("normalizes top-level PaymentView response from read_api.py", () => {
    const raw = {
      payment_id: "pay_01",
      decision: "BLOCK",
      status: "BLOCKED",
      reasons: [
        { code: "merchant_not_allowed", message: "Merchant megadeals not allowed", source: null },
        { code: "mixed_categories", message: "Mixed categories in cart", source: null },
      ],
      risk_score: "0.912000",
      judge: {
        match: false,
        score: "0.950000",
        reason: "Hidden instruction attempted $499 purchase on megadeals",
      },
      merchant_id: "megadeals",
      amount: "499.00",
      currency: "USD",
      items: JSON.stringify([{ sku: "md-bundle", name: "Mystery Box", unit_price: "499.00", quantity: 1 }]),
      created_at: "2026-11-10T04:00:00Z",
    };

    const result = normalizePayment(raw);
    expect(result.payment_id).toBe("pay_01");
    expect(result.decision).toBe("BLOCK");
    expect(result.status).toBe("BLOCKED");
    expect(result.risk_score).toBeCloseTo(0.912);
    expect(result.judge?.match).toBe(false);
    expect(result.judge?.intent_verdict).toBe("MISMATCH (BLOCK)");
    expect(result.reasons).toContain("merchant_not_allowed");
    expect(result.items.length).toBe(1);
    expect(result.items[0].sku).toBe("md-bundle");
  });

  it("normalizes nested decision payload from payment.decided audit event", () => {
    const raw = {
      id: "pay_02",
      merchant_id: "soundhub",
      amount: "29.00",
      currency: "USD",
      decision: {
        decision: "ALLOW",
        status: "CAPTURED",
        risk_score: 0.04,
        reasons: [],
      },
    };

    const result = normalizePayment(raw);
    expect(result.payment_id).toBe("pay_02");
    expect(result.decision).toBe("ALLOW");
    expect(result.status).toBe("CAPTURED");
    expect(result.risk_score).toBe(0.04);
  });
});
