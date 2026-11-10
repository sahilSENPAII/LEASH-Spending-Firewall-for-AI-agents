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
- Nov 5: LiteLLM was dropped for a direct httpx call to Groq's OpenAI-compatible API. Importing it
  added about 140 MB RSS (measured 125-150 MB), too much on Render's 512 MB free tier. The provider
  is still a config change: `JUDGE_BASE_URL` takes any OpenAI-compatible endpoint, `JUDGE_MODEL` the
  model name.
- Nov 5: Groq retired llama-3.3-70b-versatile on 2026-08-16 (it now answers 404). The judge uses
  `openai/gpt-oss-120b`, verified by `scripts/smoke_judge.py` on Nov 5 (Sony XM5 matched, 3 x $100
  gift cards did not). Fallbacks: `openai/gpt-oss-20b`, then `qwen/qwen3.8-27b`, switched with
  `JUDGE_MODEL` only.

## D8. Step-up approval through our own web page (not Telegram)
- No bot setup; reuses the frontend; works in a phone browser.
- Nov 10: the approval page is server-rendered by the backend (no separate frontend deploy needed),
  protected by a single-use random token stored hashed.

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

## D11. Pin the MCP Python SDK to 1.x (`mcp<2`)
- Spike 2 (Oct 30): pip pulled mcp 2.x, which renamed FastMCP to MCPServer and changed other APIs.
- 1.x FastMCP is stable, widely documented and what most examples use. Revisit 2.x after submission.

## D12. Money sums are exact; any rounding means BLOCK
- Amounts are `Decimal`, never float. But Decimal arithmetic still rounds to the active context
  (28 digits by default, and a caller can lower it). Nov 2: `1E+30 + 0.01` rounded back to `1E+30`,
  so a fully spent mandate passed the budget check.
- `backend/app/policy.py` adds `budget_used + amount` in a private exact context that raises `Inexact`
  instead of rounding; the evaluator turns that into BLOCK (D3). Comparisons need no context, they are exact.
- Rejected: an upper bound on amounts alone (still depends on the caller's context), floats or integer
  cents (cents would work but every module would have to convert; Decimal is what PayPal's API strings map to).

## D13. One hash chain over all audit events; any unhashed row breaks it
- `app/audit.py` links every `audit_events` row to the one before it (SHA-256 over the previous hash
  plus canonical JSON of the entry). Editing, deleting or reordering a row is reported by
  `verify_chain` / `GET /audit/verify` with the first bad id.
- Every audit write must go through `append_audit`. A row written directly has no hash: verification
  fails at it and `append_audit` refuses to extend it (fail closed, D3), instead of skipping it.
  Rejected: skipping unhashed rows, because then nulling a row's hash would hide it from the check.
- A Postgres advisory lock serializes appends so two requests cannot link to the same previous entry.
- Known limit: deleting the newest entries (truncating the tail) leaves a valid shorter chain. For the
  demo the console shows the entry count; production would publish the head hash somewhere external.

## D14. The demo attack agent lives in `agent/` with its own dependency list
- `agent/pyproject.toml` lists LangGraph, the LangChain MCP adapter, Groq and Playwright. The backend,
  its CI job and the Render deploy stay free of the browser and LLM packages. Same repo, own folder.
- The agent reaches Leash only through the MCP tool `request_payment`, like any outside agent would.
  `AGENT_LEASH_URL` empty = start the backend MCP server over stdio; a URL = HTTP. One setting.
- Two brains: `scripted` (deterministic, used by tests and the recorded demo, so the attack happens
  every time) and `groq` (a real LLM, for realism; it may or may not fall for the injection).
- The agent is deliberately naive: it reads hidden page text and obeys it. The defence is Leash's
  check on the payment, not the agent's judgement (D2).
- The agent sends only `items=[{sku, quantity}]`; Leash prices them from its catalog. (A fallback
  for the old `amount` shape existed until Sahil's catalog switch merged; removed Nov 5.)
- Demos use Sahil's real stores (`python -m stores.serve`). The agent keeps its own copy of the
  pages (`agent/standin_stores/`) so its tests run without the stores.

## D15. Anomaly model in the backend, XGBoost's own SHAP values, synthetic data
- `backend/app/anomaly.py` holds the feature list, `build_features` and `score_anomaly`;
  `backend/scripts/train_anomaly.py` generates the data and trains. One feature list for training and
  scoring, and the orchestrator imports it directly. `ml/` keeps only a README pointing here.
- Reasons come from XGBoost's `pred_contribs` (exact TreeSHAP), not the `shap` package: same numbers,
  no heavy dependency (numba, llvmlite) in the Render deploy.
- Dependency: `xgboost-cpu` (2-6 MB wheel) everywhere except macOS, which has no `xgboost-cpu`
  wheel and gets plain `xgboost` (2.5 MB there). The full Linux `xgboost` wheel is about 58 MB.
- Data is synthetic and labelled so (no real hijacked-agent data exists). Training is deterministic
  (fixed seed, one thread) so the committed model can be rebuilt byte for byte.
- The model only runs after the policy said ALLOW and can only make a decision stricter (D2); any
  scoring error means STEP_UP (D3).
