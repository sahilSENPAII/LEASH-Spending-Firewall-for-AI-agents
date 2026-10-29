# Decisions

One entry per decision: what we chose, what we rejected, why. Newest at the bottom.

## D1. Python 3.12 + FastAPI for the backend (not Go like Murmur)
- The AI side (XGBoost, SHAP, MCP Python SDK, LiteLLM, LangGraph) is Python-native.
- Fastest language for the author; ~13 days to ship.

## D2. Deterministic rules run before any LLM or ML check
- Rules are cheap, testable and cannot be prompt-injected. Any rule failure = BLOCK.
- LLM and ML only add signal on top; they can never turn a rule BLOCK into ALLOW.

## D3. Fail safe
- If a check errors or times out (LLM, model, DB), the outcome is BLOCK or STEP-UP, never ALLOW.

## D4. Authorize first, capture later (PayPal intent=AUTHORIZE)
- Money is held, not taken, until the purchase is confirmed. On failure we void (saga compensation).

## D5. Postgres only — no Redis, no Render Workflows
- Velocity counters and durable approval waits live in Postgres. Fewer moving parts.

## D6. Zero spend
- Only free tiers and hackathon-provided credits. Never add a card anywhere.
- PayPal sandbox, Groq free tier, Neon free Postgres (does not expire during judging, Dec 1–15),
  Render via hackathon credits, GitHub Actions (public repo), AG Grid Community, local Chromium.

## D7. Groq (llama-3.3-70b-versatile) for the intent judge, via LiteLLM
- Free with no card, ~1,000 requests/day on the 70B model, very low latency.
- Gemini free tier was ~20 requests/day on Flash — too few to develop against.
- Fallback: llama-3.1-8b-instant on Groq, then local Ollama. LiteLLM makes it a config change.

## D8. Step-up approval through our own web page (not Telegram)
- No bot setup; reuses the frontend; works in a phone browser.

## D9. Sandbox accounts are US-based, currency USD
- Full feature coverage (Vault, authorize/capture, payee routing). PayPal India restricts domestic payments.

## D10. All funds settle to the app's own sandbox merchant; Leash tracks the store itself
- Spike 1 (Oct 30): Vault v3 saved wallet -> AUTHORIZE with no buyer click -> capture COMPLETED, void OK,
  same PayPal-Request-Id returns the same order (idempotent). All work.
- Setting `payee` to another merchant with a vault token fails: `BILLING_AGREEMENT_NOT_FOUND`.
  The saved wallet's agreement is only with the app's own merchant account.
- Real multi-merchant routing needs PayPal's partner/multiparty onboarding — out of scope.
- So: orders carry no `payee`; the store (SoundHub / MegaDeals) is stored as Leash's `merchant_id` and in
  the order `description` / `custom_id`. Allow/block per store is unchanged for the demo.
