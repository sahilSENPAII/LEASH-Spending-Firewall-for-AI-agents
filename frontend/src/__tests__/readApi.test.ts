import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchAllAuditLog, normalizePayment } from "../api/readApi";

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
    expect(result.judge?.intent_verdict).toBe("MISMATCH");
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

describe("judge verdict", () => {
  it("does not call a STEP_UP intent mismatch a BLOCK", () => {
    const result = normalizePayment({
      payment_id: "pay_03",
      decision: "STEP_UP",
      status: "AWAITING_APPROVAL",
      reasons: [{ code: "intent_mismatch", message: "Item is a speaker", source: null }],
      judge: { match: false, score: "0.950000", reason: "Item is a speaker" },
    });
    expect(result.decision).toBe("STEP_UP");
    expect(result.judge?.intent_verdict).toBe("MISMATCH");
    expect(result.judge?.intent_verdict).not.toContain("BLOCK");
  });
});

describe("fetchAllAuditLog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("follows after_id until a short page, so the newest entries are included", async () => {
    const rows = Array.from({ length: 5 }, (_, i) => ({
      id: i + 1,
      created_at: null,
      event_type: "payment.decided",
      payment_id: null,
      payload: {},
      prev_hash: null,
      hash: null,
    }));
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        const query = new URL(url).searchParams;
        const after = Number(query.get("after_id"));
        const limit = Number(query.get("limit"));
        const page = rows.filter((r) => r.id > after).slice(0, limit);
        return new Response(JSON.stringify(page), { status: 200 });
      })
    );

    const result = await fetchAllAuditLog(2, "http://api.test");
    expect(result.map((r) => r.id)).toEqual([1, 2, 3, 4, 5]);
    expect(urls).toEqual([
      "http://api.test/audit?after_id=0&limit=2",
      "http://api.test/audit?after_id=2&limit=2",
      "http://api.test/audit?after_id=4&limit=2",
    ]);
  });
});
