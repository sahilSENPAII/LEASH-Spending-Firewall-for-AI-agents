# CONTEXT.md — shared handoff log

Two people (Sahil + teammate) work on this repo. This is their shared handoff log. **Keep it current.**

## Human rules
1. **Before starting work:** `git pull`, then read this whole file plus `docs/DECISIONS.md`.
2. **Claim work:** move the task to "In progress" with your name before you start, then push the
   claim on `main` so the other side doesn't pick the same task.
3. **After every meaningful change** (a feature, fix, decision, or end of a session): update
   "Current state", "In progress", "Next up", add a log entry at the **top** of the Log, then push it on
   `main` (rule 8).
4. Log entries: date (IST), who (person + agent), what changed, files touched, anything left half-done,
   anything the other side must know (new env vars, migrations, broken things). For Anurag's work,
   use the attribution `Anurag + Claude + Codex`.
5. Decisions with trade-offs go in `docs/DECISIONS.md`; concepts and interview notes in `docs/LEARN.md`.
6. Never put secrets here. New env vars: add the *name* to `.env.example` and mention it in the log.
7. Small commits, push often. If `git pull` conflicts in this file, keep both sides' entries.
8. `CONTEXT.md` (human handoff) and `AGENTS.md` (agent instructions) are edited and pushed
   directly on `main`. Code goes on the `sahil` / `anurag` branches → PR → `main`. Code
   branches do not touch `CONTEXT.md` or `AGENTS.md`.

## Project in one paragraph
Leash is a spending firewall for AI agents (PayPal AI Hackathon 2026, deadline Nov 13 1:30 AM IST,
we submit Nov 12 by 6 PM IST). It is an MCP server between any AI agent and PayPal. Every payment is
checked against a user-signed mandate → ALLOW / STEP-UP / BLOCK, and logged in a hash-chained audit log.
Stack: Python 3.12, FastAPI, Postgres (Neon), MCP Python SDK 1.x, PayPal REST v2 sandbox, Groq via
LiteLLM, XGBoost + SHAP, LangGraph + Playwright demo agent, React + AG Grid. Zero spend (see D6).

Key docs: `docs/STAGE1_REQUIREMENTS.md` (what and why), `docs/DECISIONS.md` (D1–D15),
`docs/LEARN.md` (concepts), `spikes/README.md` (how to run the M0 tests).

## Milestones
| ID | Dates | Goal | Status |
|---|---|---|---|
| M0 | Oct 30–31 | Setup + 3 de-risking spikes | **Done** |
| M1 | Oct 31–Nov 1 | Thin end-to-end flow + deploy: Claude pays $1 sandbox via hosted Leash | Local flow **done** (Claude Desktop paid $1 sandbox through the orchestrator); hosted deploy pending (Sahil, Render) |
| M2 | Nov 2–3 | Mandates (Ed25519), policy engine, state machine, idempotency, saga, hash-chained audit | **Mostly done**: policy, signed mandates, budget holds, audit chain (Anurag); state machine, idempotency keys, void on capture failure (Sahil). Orchestrator switch to `check_and_reserve` / `release_budget` / `append_audit` and migration `0002` on Neon **done** (Nov 3). Budget lock proven on Neon under concurrency; saga reconcile added (Nov 4) |
| M3 | Nov 4–5 | Attack demo: SoundHub + MegaDeals stores, LangGraph agent, injection blocked | Real-store agent integration merged into `main` as `aaf269b`; agent sends SKU/quantity only; 50 agent tests pass. |
| M4 | Nov 6–7 | Groq intent judge + XGBoost/SHAP anomaly model | Anomaly model merged into `main` as `4abf400`; 24 tests included in 354 passing backend tests; orchestrator wiring pending Sahil |
| M5 | Nov 8 | Step-up approval via web page, survives restart | — |
| M6 | Nov 9–10 | React frontend + AG Grid audit console. **Feature freeze Nov 10 23:59** | — |
| M7 | Nov 11 | Hardening, README, demo video | — |
| M8 | Nov 12 | Devpost submission | — |

## Current state
- M0 complete. Repo layout exists. `backend/app/policy.py` now contains a standalone deterministic evaluator; `agent/`, `stores/`, `frontend/`, and `ml/` remain placeholders.
- The policy evaluator is integrated into `backend/app/orchestrator.py`, which persists payment and audit state in Postgres. The main orchestrator now uses `check_and_reserve` / `release_budget` / `append_audit`; it removed `DEMO_MANDATE` and `_usage()` in the Sahil merge (`31dcdfb`).
- Mandates part 1: `app/canonical.py` (canonical JSON) and `app/mandates.py` (`MandateTerms` including signed `intent`, `MandateRecord` with `budget_used`/`payment_count`, Ed25519 sign/verify, and fail-closed `verify_mandate`). The 202 backend tests pass.
- Mandates part 2 is implemented and pushed on `anurag` (`f3de389`, merged into `main` at `f50199f`): `mandates` and `budget_holds` tables, mandate CRUD, atomic budget holds, and velocity counting. The 235-test suite passes.
- Audit chain is implemented in `backend/app/audit.py`: `append_audit` links entries with SHA-256 over the previous hash and canonical JSON under a Postgres advisory lock; `verify_chain` returns `ChainCheck`, and `GET /audit/verify` is available. Mandate create/revoke write audit events, and mandate, hold, and audit timestamps are stored in UTC. D13 records the chain rules and tail-truncation limit. The 271-test suite passes.
- Neon migration `0002` is at head and signed `m_demo` was seeded by Sahil on Nov 3. Do not rerun
  the migration or seed on Neon.
- One shared `MANDATE_SIGNING_KEY` is in use for Neon and was shared by DM. Keep the value only in
  ignored local `.env` files; provision it securely to Render.
- The orchestrator switch is merged (`31dcdfb`): every payment holds budget with `check_and_reserve`, releases it on FAILED/VOIDED, and writes audit events through `append_audit`. Payments on Neon are safe again.
- Task split agreed (`task-split.md`): Sahil = money path + Groq judge; Anurag = trust path + XGBoost/SHAP + LangGraph agent.
- Workflow docs are split: human handoff rules live in `CONTEXT.md`; agent-specific rules live in `AGENTS.md`. Both are maintained directly on `main`; code goes on task branches and through PRs.
- The policy evaluator's 109 unit tests and exact Decimal budget check are now merged to `main`.
- `backend/tests/test_policy.py` passes Sahil's Ruff 0.17.3 CI rules; the lint fix is merged to `main`.
- README test instructions and D12's exact-budget decision are now merged into `main` from `anurag`.
- Contracts agreed (Nov 2): Sahil said yes to all 4 open questions and the `check_and_reserve` design.
  Section 5 and `payment.blocked` are in `docs/CONTRACTS.md`; Sahil updated the app paths and renamed
  `payment.error` to `payment.failed`. The merged orchestrator records `void_failed` if capture and
  void both fail.
- Sahil: real Claude Desktop -> orchestrator -> PayPal sandbox $1 payment to SoundHub captured; MegaDeals and $250 blocked by `policy.py` (149 tests pass).
- Policy engine moved to `backend/app/policy.py` (import `app.policy`). Root `pytest.ini` and `backend/requirements-dev.txt` removed. Tests and lint only via `uv run pytest` / `uv run ruff check app tests scripts` from `backend/` (149 pass).
- Sahil agreed: migration `0002` includes a clearly named one-off step that deletes all `audit_events` rows (test data only); `payments` is untouched.
- PayPal sandbox works: Vault v3 saved wallet → AUTHORIZE (no buyer click) → capture / void,
  idempotent retries confirmed. All funds settle to the app's own merchant account (D10).
- MCP stdio server works with Claude Desktop (spike). `mcp` pinned to `<2` (D11).
- Sahil's backend app, orchestrator, MCP tool, database models, Alembic `0001`, and CI are merged into `main` from `sahil`.
- PayPal API is **not** reachable from Claude's cloud sandbox: anything calling PayPal must be run on a
  laptop.

- Trust-path polish is merged to `main`: a missing or malformed `MANDATE_SIGNING_KEY` is logged and
  blocks with `mandate_key_unavailable`; a wrong but well-formed key logs signature verification
  failure and blocks with `mandate_signature_invalid`. Key parsing is cached; shared UTC helper
  `app/times.py` and shared test setup `tests/conftest.py` replace duplicates. `docs/CONTRACTS.md`
  documents errors, one key per database, audit writes through `append_audit`, and lock order. README
  has database setup steps. `gen_mandate_key.py` now says one key per database. 274 tests pass.

- Read-only setup checker: `cd backend && uv run python -m scripts.check_setup` reports database
  reachability, a shareable signing-key fingerprint, the current migration, `m_demo` signature, and
  audit-chain validity; it never writes. Neon check passed 5/5 on Anurag's machine with the shared-key
  fingerprint `cd628e356acd` (Nov 5).

- Read-only console APIs are merged into `main` as `783a1ed`: `GET /payments`,
  `GET /payments/{id}`, paged `GET /audit`, and `GET /audit/verify` in `backend/app/read_api.py`.
  Money is returned as strings and timestamps as UTC ISO-8601 with `Z`; `risk_score` and `judge` are
  read from the latest `payment.decided` audit payload. CORS uses `CORS_ORIGINS`; backend is pinned to
  Python 3.12. Backend tests: 363 passed, 1 skipped; Ruff clean.

- Sahil's answers (Nov 5, per the supplied handoff): per-SKU categories; mixed-category carts BLOCK
  with their own reason; `md-giftcard-100` is a $100.00 MegaDeals SKU; the demo stores follow the
  agent's stand-in pages; anomaly wiring follows the items switch (STEP_UP >= 0.5, BLOCK >= 0.9,
  final thresholds by Nov 8); sync 2 is Nov 6 evening IST. `attack` on Neon is allowed; run `normal`
  only with Sahil.
- Sahil's shared catalog, items-based `request_payment`, and SoundHub/MegaDeals stores were merged to
  `main` in `b8690bb`; `backend/app/config.py` retains both `catalog_path` and `cors_origins`. His
  anomaly-wiring commit `820d249` remains only on `origin/sahil`.
- The agent now sends only `items=[{sku, quantity}]` to Leash; its old `amount` fallback is removed.
  The README demo uses Sahil's real stores; `agent/standin_stores/` remains for tests and offline runs.
  The supplied real-store demo report says the attack is BLOCKED (`mixed_categories`). Agent tests
  pass (50); backend tests remain at the prior result (464 passed, 1 skipped).
- Hosted agent MCP auth: when `AGENT_LEASH_URL` is set, the agent sends `AGENT_LEASH_TOKEN` as
  `Authorization: Bearer ...`; stdio mode is unchanged. The supplied handoff reports a local-Postgres
  rehearsal with the real backend and stores: attack BLOCKED (`mixed_categories`) and
  `/audit/verify` OK. It reports 487 backend tests passed (1 skipped) and 53 agent tests passed.
  In this workspace, 52 agent tests passed excluding `test_e2e_backend.py`, which stalled on both
  this commit and unmodified `origin/main`.
- The agent end-to-end test accepts `merchant_not_allowed` or `mixed_categories` while still requiring
  `BLOCK` and `BLOCKED`.

- LangGraph attack agent is implemented in `agent/` and pushed to `anurag` as `5372fa2`. It uses
  Playwright, scripted or Groq planning, stand-in stores, and Leash's MCP payment tool; D14 records
  the design. All 45 agent tests pass, including browser and backend end-to-end tests. Agent Ruff
  passes; backend tests pass (330 passed, 1 skipped), and Ruff passes for the changed MCP server.
  The stand-in attack dry-run planned the gift-card injection and sent no payment. Merged into `main`
  as `3be8391`.

- Review fixes are merged into `main` as `8cfd974`: inline-tag text stays together, JavaScript-rendered
  products are awaited, carts report errors without aborting the run, MCP sessions close after setup
  failures, duplicate pages are skipped, `check_setup` follows the newest migration, and the race test
  uses the test key fixture. Backend tests pass (356 passed, 1 skipped); agent tests pass (52); Ruff
  passes in both. The Postgres-only race test was skipped in this workspace. The supplied review report
  records a temporary PostgreSQL 16 run, including 960 concurrent payments; that run was not repeated
  here. The stand-in dry-run could not start because Playwright's Node driver ran out of memory.

- Anomaly model v2 was merged into `main` as `8cf1f50`. Its synthetic training data varies purchase
  hours and includes honest purchases near the limit. Across 24 UTC hours, the honest first-purchase
  score peaks at `0.108139`, the honest second-purchase score at `0.121295`, and the burst score stays
  above `0.999155`; hold-out AUC is `0.9841`. All 96 anomaly tests pass. The new hour checks produce
  32 failures against v1. The end-to-end backend harness pins time to 09:00 UTC and sets anomaly and
  velocity thresholds explicitly. Current backend suite: 464 passed, 1 skipped; agent suite: 52 passed;
  Ruff passes in both. Sahil's anomaly wiring remains on `origin/sahil` (`820d249`).

## Env vars (names only — values live in each person's local `.env`)
`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_BASE_URL`, `SANDBOX_BUYER_EMAIL`,
`MERCHANT_SOUNDHUB_EMAIL`, `MERCHANT_MEGADEALS_EMAIL`, `PLATFORM_MERCHANT_EMAIL`, `GROQ_API_KEY`,
`PAYPAL_VAULT_ID` (saved demo-buyer wallet token), `DATABASE_URL`, `DATABASE_URL_POOLED` (Neon),
`MANDATE_SIGNING_KEY` (base64 Ed25519 private key; generate once per database with `uv run python -m scripts.gen_mandate_key` from `backend/`, then provision the same key securely for Sahil, Anurag, and Render; a different key makes signature verification fail and blocks payments),
`VELOCITY_MAX_PAYMENTS` (default 3), `VELOCITY_WINDOW_SECONDS` (default 600),
`CORS_ORIGINS` (comma-separated browser origins; default `http://localhost:5173`), `AGENT_LEASH_URL`,
`AGENT_LEASH_TOKEN` (bearer token for hosted Leash; same value as server-side `MCP_AUTH_TOKEN`),
`AGENT_MANDATE_ID`, `AGENT_SOUNDHUB_URL`, `AGENT_MEGADEALS_URL`, `AGENT_BRAIN`, `AGENT_CHROMIUM_PATH`.
Ask Sahil for the shared sandbox values.

## In progress
- Anurag + Claude + Codex: hosted Leash bearer-token support (`14c0bab`) is merged; the agent sends
  `AGENT_LEASH_TOKEN` only when using HTTP, while stdio remains unchanged. Ruff is clean; 52 tests
  pass when the baseline-stalling backend E2E test is excluded. Next planned work is the AG Grid
  console in `frontend/`.
- Sahil + Claude: Render deploy of the backend with hosted MCP at `/mcp` (token `MCP_AUTH_TOKEN`), then
  the hosted $1 test (sync 1); then Groq intent judge (`app/judge.py`) and step-up approvals (M5).

## Next up
- [x] Sahil: Python 3.12 via uv; Neon project `leash` (AWS us-east-1), `DATABASE_URL` in `.env`.
- [x] Unit tests and budget fix for `backend/policy.py` (Anurag + Claude; merged into `main` from branch `anurag`, Nov 2).
- [x] README test instructions and D12 exact-budget decision (merged from `anurag` into `main`, Nov 2).
- [x] Both: agree `docs/CONTRACTS.md` (Nov 2). Sahil: section 5 + `payment.blocked`.
- [x] Sahil: `sahil` branch merged into `main` (Nov 2).
- [x] Anurag: move the policy engine to `backend/app/policy.py`; remove root `pytest.ini` and `backend/requirements-dev.txt`; tests and Ruff pass from `backend/`.
- [x] Anurag: add `cryptography` to `backend/pyproject.toml` and `uv.lock` for mandate signing.
- [x] Sahil: merchant/category config (server-side, never from the agent; `MERCHANT_CATEGORIES` in `backend/app/orchestrator.py`).
- [x] Anurag: mandates part 2 — database model/migration, create/revoke/list, and reserve/release budget (pushed to `anurag` as `f3de389`; merged into `main`).
- [x] Anurag: hash-chained audit log + `/audit/verify` (merged into `main` as `88bf54c`).
- [x] Both: agree on one shared `MANDATE_SIGNING_KEY` for the Neon database (shared by DM) and choose who runs migration `0002` (Sahil).
- [x] Sahil: run migration `0002` and seed `m_demo` on Neon (Nov 3).
- [x] Anurag: run the read-only setup checker against Neon; 5/5 checks passed with shared-key
      fingerprint `cd628e356acd` (Nov 5).
- [ ] Sahil: run `uv run python -m scripts.check_setup` on your machine and on Render after deploy;
      the key fingerprint must match Anurag's.
- [x] Sahil: orchestrator switch to `check_and_reserve` / `release_budget` / `append_audit`; payment row
      flushed first, mandate-before-audit lock order, UTC `now`, no floats, `DEMO_MANDATE` and
      `_usage()` removed (merged to `main` as `31dcdfb`, Nov 3).
- [x] Sahil: merge shared-catalog pricing, items-based `request_payment`, and demo stores into `main`
      (`b8690bb`); `catalog_path` and `cors_origins` are both present in `backend/app/config.py`.
- [ ] Sahil (minor): fix the three `0001` nullable mismatches.
- [x] Sahil: Postgres concurrency test for `check_and_reserve` (passed on Neon) and saga reconcile
      (`app/reconcile.py`, `scripts/reconcile_payments.py`), merged to `main` as `24dbb07` (Nov 4).
- [ ] Sahil: run `reconcile_payments` once against PayPal sandbox (not yet run against Neon/PayPal);
      decide whether to schedule it; cover `AWAITING_APPROVAL` once M5 approvals exist.
      Prices come from the shared catalog; the agent sends no price or amount. Update
      `docs/CONTRACTS.md` section 5.5.
- [x] Sahil: shared-catalog SoundHub and MegaDeals demo stores with hidden injection, merged in
      `b8690bb`.
- [ ] Sahil (~Nov 5): Render deploy with HTTP MCP at `https://<service>.onrender.com/mcp`.
- [x] Sahil: update the `docs/CONTRACTS.md` app paths.
- [x] Sahil: rename `payment.error` to `payment.failed`.
- [x] M1 backend skeleton: FastAPI app + settings, Postgres models + Alembic, PayPal client, CI
      (merged into `main` from `sahil`).
- [x] Sahil: MCP `request_payment` + orchestrator (merged into `main` from `sahil`).
- [x] Sahil: MCP `list_mandates` / `get_payment_status` + streamable HTTP at `/mcp` behind
      `MCP_AUTH_TOKEN` (merged to `main` in `c0254f7`, Nov 5).
- [x] Anurag + Claude + Codex: merge the attack-agent commit from `anurag` directly into `main` (`3be8391`,
      Nov 4).
- [x] Anurag + Claude + Codex: review fixes for agent pages, per-cart errors, `check_setup`, MCP
      cleanup, and the race-test key fixture (pushed to `anurag`, merged into `main` as `8cfd974`).
- [x] Anurag + Claude + Codex: add read-only `GET /payments`, `GET /payments/{id}`, `GET /audit`,
      and `GET /audit/verify` APIs, `CORS_ORIGINS`, and backend Python 3.12 pin (`783a1ed`); 363
      backend tests passed, 1 skipped, Ruff clean.
- [ ] Both: sync 2, Nov 6 evening IST (attack blocked end to end on Neon).
- [x] Anurag + Claude + Codex: remove the old `amount` fallback; use Sahil's real stores for demos
      while keeping `agent/standin_stores/` for tests/offline runs (`aaf269b`).
- [x] Anurag + Claude + Codex: send `AGENT_LEASH_TOKEN` as the bearer header when using hosted
      `/mcp`; stdio remains unchanged (`14c0bab`).
- [ ] Sahil: after Render deploy, share `MCP_AUTH_TOKEN` by DM; Anurag sets
      `AGENT_LEASH_URL=https://<service>.onrender.com/mcp` and `AGENT_LEASH_TOKEN`, then runs `attack`.
- [ ] Sahil: write `risk_score` (string) and `judge` into the `payment.decided` audit payload when
      the model and judge run; the console reads them from there.
- [x] Anurag: run `uv run python -m leash_agent attack --standin` against Neon; Leash BLOCKED the
      injected cart, `/payments` showed it, and `/audit/verify` returned 12 valid entries (Nov 5).
- [x] Anurag: retrain the anomaly model with realistic time-of-day and near-limit examples, add the
      72 hour-by-hour checks, and pin the agent E2E clock and thresholds (`8cf1f50`).
- [ ] Sahil: merge anomaly wiring from `sahil` (`820d249`) now that model v2 is on `main`; thresholds
      are STEP_UP at 0.5 and BLOCK at 0.9, with STEP_UP on risk-check errors.
- [ ] Anurag (Nov 5–6): AG Grid audit console in `frontend/` on top of the read routes.

## Log (newest first)

### 2026-11-05 03:20 IST — Sahil + Claude
- Merged to `main` (`c0254f7`): anomaly wiring (`820d249`) on top of Anurag's model v2, and hosted MCP
  (`ddd821f`). Backend and agent suites pass at night-time IST with model v2.
- Anomaly: STEP_UP at >= 0.5, BLOCK at >= 0.9, any error -> STEP_UP; `risk_score` is a 6-decimal string;
  new codes `anomaly_step_up`, `anomaly_block`, `anomaly_check_failed`. STEP_UP payments stay
  `AWAITING_APPROVAL` with the hold kept until M5 approvals exist.
- MCP: new tools `list_mandates` (returns `{"mandates": [...]}`) and `get_payment_status`; streamable
  HTTP at `/mcp` (stateless JSON) needing `Authorization: Bearer <MCP_AUTH_TOKEN>` (401 bad/missing,
  503 when unset). Stdio for Claude Desktop unchanged. `MCP_AUTH_TOKEN` generated by Sahil and shared
  by DM; Render needs it too.
- `docs/CONTRACTS.md` 5.2–5.7 updated to match.
- Files: `CONTEXT.md`, `docs/CONTRACTS.md`.

### 2026-11-05 03:18 IST — Anurag + Claude + Codex
- Added `AGENT_LEASH_TOKEN`; hosted agent requests now send `Authorization: Bearer <token>`, while
  stdio is unchanged. Updated `.env.example` and README; added token-config and HTTP MCP tests.
- Pushed code commit `14c0bab` to `anurag` and fast-forwarded it into `main`. Agent tests: 52 passed
  with `test_e2e_backend.py` excluded; that test also stalled on unmodified `origin/main`. Ruff passed.
- The supplied handoff reports a separate 53-pass agent run, 487 backend tests passed (1 skipped),
  and a local-Postgres rehearsal. Sahil's DM has not been sent. Added an empty `AGENT_LEASH_TOKEN` to
  the ignored local `.env`; `.env` is not tracked.
- Files: `.env.example`, `README.md`, `agent/leash_agent/config.py`,
  `agent/tests/{test_config,test_leash_http}.py`, `CONTEXT.md`.

### 2026-11-05 02:54 IST — Anurag + Claude + Codex
- Removed the agent's old `amount` fallback; it now sends only SKU and quantity. Updated the README
  demo to use Sahil's real stores. The code commit `aaf269b` was pushed to `anurag` and fast-forwarded
  into `main`.
- Agent tests: 50 passed; `uv run ruff check leash_agent tests` passed. The live Neon demo was not
  rerun because it writes a payment record; the supplied run report records BLOCK (`mixed_categories`).
- Files: `README.md`, `agent/leash_agent/{graph,leash}.py`, `agent/standin_stores/README.md`,
  `agent/tests/{stub_leash_server,test_leash}.py`, `docs/DECISIONS.md`, `CONTEXT.md`.

### 2026-11-05 02:32 IST — Anurag + Claude + Codex
- Updated the synthetic anomaly data and committed model as v2 (`8cf1f50`); fixed the agent backend
  harness at 09:00 UTC and pinned anomaly/velocity settings in the E2E test. The new hour checks fail
  on v1 (32 failed, 40 passed) and pass on v2; the full anomaly module passes (96).
- Backend: 464 passed, 1 skipped; agent: 52 passed; Ruff clean in both. In a disposable merge with
  `origin/sahil` (`820d249`), the backend suite passed (473, 1 skipped); the simulated agent run
  stalled in its E2E subprocess and was interrupted. No result is claimed for that simulated agent run.
- Merged the model fix into `main`; Sahil's catalog/stores/items switch is already in `main` (`b8690bb`),
  while anomaly wiring remains on `sahil` (`820d249`).
- Files: `agent/tests/backend_harness.py`, `agent/tests/test_e2e_backend.py`,
  `backend/app/anomaly_model.json`, `backend/scripts/train_anomaly.py`, `backend/tests/test_anomaly.py`,
  `CONTEXT.md`.

### 2026-11-05 02:15 IST — Sahil + Claude
- Merged to `main` (`b8690bb`): shared catalog `stores/catalog.json` (new setting `CATALOG_PATH`), demo
  stores (`python -m stores.serve`, SoundHub :8101, MegaDeals :8102; hidden injection on MegaDeals
  `md-sony-xm5`), and `request_payment(mandate_id, merchant_id, items=[{sku, quantity}])` priced by
  Leash. New BLOCK reason codes: `empty_cart`, `unknown_sku`, `sku_wrong_merchant`, `invalid_quantity`,
  `mixed_categories`. `docs/LEARN.md` money-path section added. `config.py` conflict resolved by keeping
  both `catalog_path` and `cors_origins`. Backend 392 passed, 1 skipped; agent tests pass.
  `docs/CONTRACTS.md` 5.2–5.5 and 5.7 updated to match. Anurag can drop the old-shape fallback.
- Anomaly wiring committed on `sahil` (`820d249`), NOT merged: model after T1 in its own session;
  STEP_UP at >= `ANOMALY_STEP_UP_THRESHOLD` (0.5), BLOCK at >= `ANOMALY_BLOCK_THRESHOLD` (0.9), any error
  or out-of-range score -> STEP_UP; `risk_score` as a 6-decimal string; new codes `anomaly_step_up`,
  `anomaly_block`, `anomaly_check_failed`. Backend 401 passed.
- Blocking finding: the real model scores the normal $179 first SoundHub purchase >= 0.5 from 19:00 to
  02:00 UTC (00:30–07:30 IST) because of the hour-of-day feature, so honest purchases get STEP_UP at
  night and `agent/tests/test_e2e_backend.py` fails when CI runs then (0.60 at 20:28 UTC; passes with
  threshold 0.7). Asked Anurag to retrain so the hour weighs much less, and to make the e2e test
  independent of the clock. The wiring merges after that.
- Files: `CONTEXT.md`, `docs/CONTRACTS.md`.

### 2026-11-05 01:54 IST — Anurag + Claude + Codex
- Updated the agent backend end-to-end assertion to accept `merchant_not_allowed` or
  `mixed_categories`, while retaining the BLOCK decision and BLOCKED status checks. Commit `6d05bfd`
  was pushed to `anurag` and fast-forwarded into `main`.
- Agent: 52 passed; `uv run ruff check leash_agent tests` passed. Sahil's catalog/store changes remain
  on `origin/sahil`; preserve both `CORS_ORIGINS` and `CATALOG_PATH` if the config merge conflicts.
- Files: `agent/tests/test_e2e_backend.py`, `CONTEXT.md`.

### 2026-11-05 01:40 IST — Anurag + Claude + Codex
- Installed Playwright Chromium and ran the scripted stand-in attack against Neon. Leash BLOCKED the
  MegaDeals cart (`merchant_not_allowed`, `amount_exceeds_max_payment_amount`); no capture was made.
- `GET /payments` showed the BLOCKED $449.00 payment; `GET /audit/verify` returned `ok: true`,
  12 checked entries, and no bad id. The latest audit event was `payment.blocked` for that payment.
- Files: `CONTEXT.md`.

### 2026-11-05 01:36 IST — Anurag + Claude + Codex
- Verified Neon from Anurag's machine: 5/5 checks passed; database reachable, shared signing-key
  fingerprint `cd628e356acd`, migration `0002`, `m_demo` signature valid, and all 10 audit entries
  verified.
- Files: `CONTEXT.md`.

### 2026-11-05 01:24 IST — Anurag + Claude + Codex
- Added read-only payment and audit routes, CORS configuration, the backend Python 3.12 pin, contract
  details, and README route instructions; commit `783a1ed` was pushed to `anurag` and fast-forwarded
  into `main`.
- Backend: 363 passed, 1 skipped; Ruff clean. Neon check is pending: local `.env` has no
  `DATABASE_URL`, and its signing-key fingerprint is `3c914ba04924`, not the shared fingerprint
  `cd628e356acd` supplied by Sahil. No secret values were added to the repo or changed locally.
- Files: `.env.example`, `README.md`, `backend/.python-version`, `backend/app/{config,main,read_api}.py`,
  `backend/tests/test_read_api.py`, `docs/CONTRACTS.md`, `CONTEXT.md`.

### 2026-11-05 00:29 IST — Anurag + Claude + Codex
- Removed the stale attack-agent entry from In progress and left Sahil's anomaly-wiring task unticked
  under Next up.
- Files: `CONTEXT.md`.

### 2026-11-05 00:24 IST — Anurag + Claude + Codex
- Pushed review-fix commit `8cfd974` to `origin/anurag` and fast-forwarded it into `main`.
- Backend tests: 356 passed, 1 skipped; agent tests: 52 passed; Ruff clean in both. The Postgres-only
  race test was skipped locally. The attached report records PostgreSQL 16 migration and concurrency
  checks, but those were not rerun in this workspace.
- `attack --standin --dry-run` did not start: Playwright's Node driver ran out of memory before the
  browser opened; no payment was sent. The code merge is complete, but that demo check remains pending.
- Files: `agent/leash_agent/{__main__,graph,leash,pages}.py`, `agent/tests/*`,
  `backend/scripts/check_setup.py`, `backend/tests/{test_check_setup,test_mandates}.py`, `CONTEXT.md`.

### 2026-11-05 00:16 IST — Anurag + Claude + Codex
- Claimed the review fixes for agent store-page reliability, cart error handling, setup-check migration
  detection, MCP cleanup, and the Postgres race-test key fixture.
- Files: `CONTEXT.md`.

### 2026-11-04 23:50 IST — Anurag + Claude + Codex
- Set the attribution for Anurag's Context entries to `Anurag + Claude + Codex` and corrected the
  recent Nov 4 entries.
- Files: `CONTEXT.md`.

### 2026-11-04 23:48 IST — Anurag + Claude + Codex
- Pushed `ac1f611` to `origin/anurag` and merged directly into `main` as `4abf400`, per request; no PR.
- Backend: 354 passed, 1 skipped; agent: 45 passed; Ruff clean in both projects. Sample scores:
  burst `0.999974`, demo purchase `0.045706`. The initial parallel test run hit an OpenBLAS allocation
  failure; the sequential run with BLAS threads limited passed.
- Files: anomaly model code, synthetic trainer, model, tests, dependency lock, contracts, D15, learning
  notes, `ml/README.md`; `CONTEXT.md` records the merge.

### 2026-11-04 23:43 IST — Anurag + Claude + Codex
- Applied the anomaly-model patch as commit `ac1f611` on local `anurag`: synthetic generator,
  XGBoost training, `score_anomaly`, `build_features`, model JSON, tests, D15, contract and learning
  notes, and `ml/README.md`.
- Backend: 354 passed, 1 skipped; Ruff clean. Agent: 45 passed; Ruff clean. Sample scores were
  `0.999974` for the burst and `0.045706` for the demo purchase. Backend checks needed BLAS threads
  limited after the first parallel run hit an OpenBLAS allocation failure.
- The code commit is not pushed or merged. The scorer remains unwired; Sahil owns orchestrator use.
- Files: `backend/app/{anomaly.py,anomaly_model.json}`, `backend/scripts/train_anomaly.py`,
  `backend/tests/test_anomaly.py`, `backend/{pyproject.toml,uv.lock}`, `docs/{CONTRACTS,DECISIONS,LEARN}.md`,
  `ml/README.md`, `CONTEXT.md`.

### 2026-11-04 23:37 IST — Anurag + Claude + Codex
- Claimed anomaly-model item 2 on `anurag`: synthetic data, XGBoost model, anomaly scoring and
  database feature building. The scorer is not yet wired into the orchestrator.
- Files: `CONTEXT.md`.

### 2026-11-04 23:28 IST — Anurag + Claude + Codex
- Merged `anurag` directly into `main` as merge commit `3be8391` (agent commit `5372fa2`), per request.
  No PR was created.
- Agent tests passed (45); agent Ruff passed. Backend tests passed (330 passed, 1 skipped), and the
  stand-in attack dry-run sent no payment.
- Files: `CONTEXT.md`; the agent code is recorded in the merge commit.

### 2026-11-04 23:23 IST — Anurag + Claude + Codex
- Installed Python 3.12.14, agent dependencies, and Playwright Chromium after approval.
- Agent suite: 45 passed; agent Ruff clean. Backend suite: 330 passed, 1 skipped; backend MCP Ruff
  clean. `attack --standin --dry-run` showed the injected gift-card request and sent no payment.
- Pushed `5372fa2` to `origin/anurag`. It is not merged to `main` yet.
- Files: no code changes after `5372fa2`; `CONTEXT.md` records verification and branch status.

### 2026-11-04 23:16 IST — Anurag + Claude + Codex
- Applied the LangGraph attack-agent patch as one commit on local `anurag` (`5372fa2`, 35 files):
  `agent/`, CI, README, D14, LEARN notes, and the MCP log-level option.
- Backend suite: 330 passed, 1 skipped. Ruff passes for `agent/leash_agent`, `agent/tests`, and
  `backend/app/mcp_server.py`. Agent tests were not run: Python 3.12, agent dependencies, and
  Playwright Chromium are absent; no downloads were made.
- The code commit is not pushed or merged. No Neon or PayPal payment was run.
- Files: `.env.example`, `.github/workflows/ci.yml`, `README.md`, `agent/**`,
  `backend/app/mcp_server.py`, `docs/DECISIONS.md`, `docs/LEARN.md`, `CONTEXT.md`.

### 2026-11-04 00:25 IST — Sahil + Claude
- Merged to `main` (`24dbb07`): Postgres-only race test in `tests/test_mandates.py` (runs only when
  `LEASH_PG_TEST_URL` is set in the shell; not in `.env.example` because it is read from the shell),
  saga reconcile (`app/reconcile.py`, `scripts/reconcile_payments.py`, `PayPalClient.find_order`), and the
  race test cleanup fix. 330 passed, 1 skipped (the Postgres test).
- Race test on Neon: two $6 reserves on a $10 mandate -> one ALLOW, one BLOCK `budget_exceeded`; with the
  row lock removed the test fails. `/audit/verify` stayed ok throughout.
- Neon cleanup: deleted the leftover `m_pgtest_%` rows from `payments` and `budget_holds` by SQL. The two
  revoked `m_pgtest_*` mandates stay (audit entries refer to them). `m_demo` untouched.
- Reconcile has run only against fake PayPal. Do not run it on Neon until it is tried in sandbox. Its
  `REQUESTED` check replays the create-order call, which can place a brief hold that it voids.
  Event names added to `docs/CONTRACTS.md` section 5.3.
- Files: `CONTEXT.md`, `docs/CONTRACTS.md`.

### 2026-11-03 23:57 IST — Anurag + Codex
- Added `backend/scripts/check_setup.py`, README step 8, and 12 checker tests. The checker is
  read-only and reports the database, signing-key fingerprint, migration, `m_demo`, and audit chain.
- The checker stopped at the database check because `DATABASE_URL` is absent from Anurag's local
  `.env`; Neon verification remains pending. Fingerprint: `3c914ba04924`. No migration or seed was run.
- All 287 backend tests pass; Ruff is clean. LangGraph work remains unstarted pending the user's
  materials.
- Files: `backend/scripts/check_setup.py`, `backend/tests/test_check_setup.py`, `README.md`,
  `CONTEXT.md`.

### 2026-11-03 23:52 IST — Anurag + Codex
- Claimed the read-only setup checker: verify database reachability, key fingerprint, migration, `m_demo` signature, and audit chain. No database writes.
- Files: `CONTEXT.md`.

### 2026-11-03 23:50 IST — Sahil + Claude
- Orchestrator switch merged to `main` (`31dcdfb`, from `sahil` `be6acf7`): payment row flushed first,
  `check_and_reserve` / `release_budget` / `append_audit`, `DEMO_MANDATE` and `_usage()` removed.
  275 tests pass.
- Verified on Neon with the real MCP server: $1 to soundhub -> ALLOW, CAPTURED; megadeals -> BLOCK
  `merchant_not_allowed`; `GET /audit/verify` -> `{"ok": true, "checked": 6, "first_bad_id": null}`.
- Behaviour changes: unknown mandate now blocks with `mandate_not_found`; velocity comes from
  `VELOCITY_MAX_PAYMENTS` / `VELOCITY_WINDOW_SECONDS` (defaults 3 / 600) and counts every payment that
  holds or may hold money. Render needs these env vars.
- Known gap: if a DB write fails after PayPal authorized or captured, the row can stay REQUESTED or
  AUTHORIZED while money is held or taken (needs saga cleanup). Not tested under real concurrency yet.
- Files: `CONTEXT.md`.

### 2026-11-03 23:38 IST — Anurag + Codex
- Corrected `gen_mandate_key.py` to specify one shared key per database and removed the two stray
  blank lines in section 3 of `docs/CONTRACTS.md`. No migration or seed was run; Sahil already did
  both on Neon on Nov 3.
- Recorded Sahil's Nov 3 answers: shared key and Neon setup are complete; orchestrator switch due
  Nov 4 at 2 PM IST, stores at `:8101` / `:8102` by Nov 4 evening, HTTP MCP on Render around Nov 5,
  and `request_payment` uses SKU and quantity items without agent-supplied prices.
- No LangGraph implementation started. Waiting for you to provide the materials and steps.
  274 tests pass; Ruff is clean.
- Files: `backend/scripts/gen_mandate_key.py`, `docs/CONTRACTS.md`, `CONTEXT.md`.

### 2026-11-03 23:35 IST — Anurag + Codex
- Claimed the pre-agent cleanup: correct the signing-key guidance and remove two stray contract blank lines. Code/docs changes will be on `anurag`; handoff updates stay on `main`.
- Files: `CONTEXT.md`.

### 2026-11-03 23:30 IST — Sahil + Claude
- Ran `alembic upgrade head` (`0002`) and `scripts.seed_demo_mandate` on Neon. Checked read-only:
  `alembic current` is `0002 (head)`, `mandates` has `m_demo`, `audit_events` has one hashed
  `mandate.created` row.
- The shared `MANDATE_SIGNING_KEY` is in Sahil's `.env` (shared by DM).
- Claimed the orchestrator switch on `sahil`. No payments on Neon until it is merged.
- Files: `CONTEXT.md`.

### 2026-11-03 23:27 IST — Anurag + Codex
- Trust-path polish: a missing or malformed `MANDATE_SIGNING_KEY` is logged and blocks with
  `mandate_key_unavailable`; a wrong valid key logs signature verification failure and blocks with
  `mandate_signature_invalid`. Key parsing is cached; shared UTC and SQLite test helpers replace copies.
- Extended `docs/CONTRACTS.md` with implemented APIs, failure behavior, and lock order; added README
  database setup instructions. Updated M1 and M2 milestones.
- All 274 tests pass; Ruff is clean. Four key logging/cache regression tests fail against the previous
  `mandates.py`. Tests ran on Python 3.14.7; Python 3.12 is not installed in this workspace.
- Files: `backend/app/{times,mandates,audit}.py`, `backend/tests/{conftest,test_mandates,test_audit}.py`,
  `docs/CONTRACTS.md`, `README.md`, `CONTEXT.md`.

### 2026-11-03 23:22 IST — Anurag + Codex
- Claimed the trust-path polish on `anurag`; code changes will stay on the code branch and handoff updates will be pushed directly to `main`.
- Files: `CONTEXT.md`.


### 2026-11-03 23:11 IST — Anurag + Claude
- Compared `audit-chain-ALL-CHANGES.txt` with the repo; the code files, tests, D13, and Learn notes were already present.
- Added the supplied mutation-check summary to the audit handoff log. No code changes were needed.
- Files: `CONTEXT.md`.

### 2026-11-03 23:08 IST — Anurag + Claude
- Merged audit chain commit `88bf54c` into `main`: `append_audit`, `verify_chain`, `GET /audit/verify`, and `mandate.created` / `mandate.revoked` events. D13 documents fail-closed handling of unhashed rows and the tail-truncation limit.
- Normalized mandate, hold, and audit timestamps to UTC before storage; naive timestamps are rejected where audit or mandate state is written. All 271 tests pass and Ruff is clean.
- The supplied audit-change notes report eight deliberate fault checks, each caught by a test: missing `prev_hash`, skipped link validation, missing UTC conversion, missing lock, extending an unhashed row, raw payload storage, non-UTC mandate times, and a missing revoke event.
- The Neon migration and seed have not been run. Sahil's orchestrator still writes audit rows directly; switch all writes to `append_audit` before any payment on Neon after the seed.
- Files: `backend/app/{audit,main,mandates}.py`, `backend/tests/test_{audit,mandates}.py`, `docs/DECISIONS.md` (D13), `docs/LEARN.md`, `CONTEXT.md`.

### 2026-11-03 22:45 IST — Anurag + Claude
- Mandates part 2 (`f3de389`) is merged into `main` (`f50199f`); 235 tests pass, Ruff clean.
- Next: agree on the shared `MANDATE_SIGNING_KEY` and who runs migration `0002` on Neon with Sahil, then build the audit chain.
- Files: `CONTEXT.md`.

### 2026-11-03 22:36 IST — Anurag + Claude
- Implemented mandates part 2 in one commit, `f3de389`, and pushed it to `anurag`; it is not merged into `main` yet. Added `MandateRow` / `BudgetHold`, migration `0002`, create/revoke/get/list, `check_and_reserve`, `release_budget`, and velocity settings.
- Migration `0002` deletes all rows in `audit_events` as the agreed one-off cleanup; it does not alter `payments`. Scratch SQLite upgrade -> downgrade -> upgrade passed, with the audit row cleared and payment preserved. The migration has not been run on Neon.
- The 235 tests pass and Ruff is clean. `alembic check` reports only the three pre-existing nullable mismatches in migration `0001` (`payments.created_at`, `payments.updated_at`, `audit_events.created_at`).
- Added the signed `m_demo` seed script and mandates notes to `docs/LEARN.md`.
- A single shared `MANDATE_SIGNING_KEY` is required for the shared database; agree on the key and who runs `0002` before migration or seeding.
- Files: `.env.example`, `backend/app/{config,mandates,models}.py`, `backend/migrations/versions/0002_mandates_and_budget_holds.py`, `backend/scripts/seed_demo_mandate.py`, `backend/tests/test_mandates.py`, `docs/LEARN.md`, `CONTEXT.md`.

### 2026-11-02 05:30 IST — Sahil + Claude
- Merged `sahil` into `main` (`1ab1401`): smoke script run note fixed (`uv run python -m scripts.smoke_paypal`);
  a failed void is now audited as `payment.failed` (was `payment.error`). 202 tests pass.
- `docs/CONTRACTS.md`: sections 1-3 paths moved to `backend/app/`; `intent` added to the signed mandate
  terms; Anurag's answers to questions A and B recorded.
- Not done yet: the `{"void_failed": true}` payload from section 5.3 (comes with the `append_audit` switch).
- Render: $50 hackathon credits applied to Sahil's account, no card on file.

### 2026-11-02 04:25 IST — Anurag + Claude
- Added canonical JSON and Ed25519 signing/verification for mandate terms, including signed `intent`; `verify_mandate` fails closed. Revocation and usage state remain unsigned.
- Added `MANDATE_SIGNING_KEY` configuration and a key generator. A local key is stored in ignored `.env`; each person and Render must generate a separate key.
- Added `cryptography` to the backend dependencies. All 202 tests pass; Ruff and app import checks pass.
- Pushed `d2a257f` to `anurag` and fast-forwarded it into `main`.
- Files: `.env.example`, `backend/app/{canonical,config,mandates}.py`, `backend/scripts/gen_mandate_key.py`, `backend/tests/test_{canonical,mandates}.py`, `backend/pyproject.toml`, `backend/uv.lock`, `CONTEXT.md`.

### 2026-11-02 04:04 IST — Anurag + Claude
- Moved `backend/policy.py` to `backend/app/policy.py`; updated imports in `orchestrator.py` and `test_policy.py`; removed `PYTHONPATH=..` from `mcp_server.py` and `..` from pytest `pythonpath`.
- Removed root `pytest.ini` and `backend/requirements-dev.txt`; README test steps now use uv. Fixed policy lint so CI covers it.
- 149 tests pass, Ruff clean, and `import app.mcp_server, app.policy` works from `backend/`.
- Files: `README.md`, `backend/app/{policy,orchestrator,mcp_server}.py`, `backend/pyproject.toml`, `backend/tests/test_policy.py`, `docs/DECISIONS.md`, `pytest.ini`, `backend/requirements-dev.txt`.

### 2026-11-02 03:50 IST — Sahil + Claude
- `sahil` was merged into `main` directly, without a PR.
- Known gaps in the merged orchestrator until the trust-path functions land: usage is read without a lock
  (two concurrent payments can both pass the budget check), and the mandate is the hardcoded `m_demo`.
- Added Sahil's next tasks: `payment.error` -> `payment.failed`, smoke script run note, MCP
  `list_mandates` / `get_payment_status`.
- Files: `CONTEXT.md`.

### 2026-11-02 03:34 IST — Anurag + Claude
- Updated the payment status to reflect it is on `main`, set Anurag's in-progress task to moving `policy.py` into `backend/app/`, and marked Sahil's merchant/category map complete.
- Files: `CONTEXT.md`.
- No tests were run; this was a documentation-only update.

### 2026-11-02 03:32 IST — Anurag + Claude
- Removed the stale “remote branch tips match” note from the current Sahil merge checklist; Sahil's code remains merged into `main`.
- Files: `CONTEXT.md`.

### 2026-11-02 03:31 IST — Anurag + Claude
- Sahil agreed the contract: all 4 open questions yes; `check_and_reserve`, release points, audit `now`, no floats. Added `payment.blocked` (Sahil to add in section 5).
- Decided: trust-path modules move into `backend/app/` after Sahil's merge; test command is `uv run pytest` from `backend/`; `cryptography` goes in `pyproject.toml`.
- Sahil owns merchant/category config.
- `sahil` is already merged into `main`; the remote branch tips match.
- Files: `CONTEXT.md`.

### 2026-11-02 03:05 IST — Anurag + Claude
- Merged `anurag` into `main`: `docs/CONTRACTS.md` draft (Anurag's half). Still a draft until Sahil fills section 5 and answers the open questions.
- Claimed mandates part 1: canonical JSON + Ed25519 sign/verify, `verify_mandate`. DB parts (mandates table, `budget_holds`, migration 0002, `check_and_reserve`) wait for `sahil` to merge.
- Files: `docs/CONTRACTS.md`.

### 2026-11-02 03:01 IST — Anurag + Claude
- Drafted Anurag's trust-path half of `docs/CONTRACTS.md` on branch `anurag`: mandate signatures, atomic budget holds, audit-chain functions, and anomaly-model shapes.
- Waiting for Sahil to fill section 5 and answer four open questions before the contract is agreed.
- Files: `docs/CONTRACTS.md`.
- No tests were run; this task changed documentation only.

### 2026-11-02 02:55 IST — Anurag + Claude
- Removed the `<name>/<task>` branch option from `AGENTS.md` to match the fixed `sahil` and `anurag` branch names and avoid the name clash.
- Files: `AGENTS.md`, `CONTEXT.md`.
- No tests were run; this was a documentation-only update.

### 2026-11-02 02:54 IST — Anurag + Claude
- Updated Rule 8 and `AGENTS.md` to name the existing `sahil` and `anurag` branches; left historical log wording unchanged.
- Files: `CONTEXT.md`, `AGENTS.md`.

### 2026-11-02 02:51 IST — Anurag + Claude
- Changed the mandates and audit Next up branch labels to the existing `anurag` branch.
- Files: `CONTEXT.md`.

### 2026-11-02 02:49 IST — Anurag + Claude
- Fixed Ruff CI errors in `backend/tests/test_policy.py`: sorted imports, used `datetime.UTC`, and wrapped the long parametrization line.
- Verified the original file had three Ruff errors under Sahil's Ruff 0.17.3 rule set; the updated file passes, and all 109 policy tests pass.
- Pushed commit `c898c39` to `origin/anurag` and fast-forwarded `main` to include it.
- Files: `backend/tests/test_policy.py`, `CONTEXT.md`.

### 2026-11-02 02:41 IST — Anurag + Claude
- Integrated Sahil's backend handoff update from `origin/main` with the policy documentation merge; retained the backend status, environment variable names, and Claude Code instructions.
- README test instructions and D12 exact-budget decision are now on `main`; the contracts task still needs the velocity-window definition for `recent_payment_count`.
- Files: `CONTEXT.md`, `CLAUDE.md`.
- No tests were run; this integration contains documentation changes only.

### 2026-11-02 02:40 IST — Anurag + Claude
- Merged `origin/anurag` into `main`; README test instructions and D12 exact-budget decision are now on `main`.
- Updated the `docs/CONTRACTS.md` next step to define `recent_payment_count` from payments inside the velocity window.
- Files: `README.md`, `docs/DECISIONS.md`, `CONTEXT.md`.
- No tests were run; the merge contains documentation changes only.

### 2026-11-02 02:40 IST — Sahil + Claude
- Branch `sahil` (pushed, not merged): backend skeleton in `backend/` (uv project, Python 3.12) with
  `app/config.py`, `app/db.py`, `app/models.py`, `app/state.py`, `app/paypal.py`, `app/main.py`,
  Alembic `0001` (`payments`, `audit_events` with `prev_hash`/`hash` columns for the audit chain),
  `scripts/smoke_paypal.py`, GitHub Actions CI. Merged `origin/main` into `sahil`.
- `backend/pyproject.toml` pytest `pythonpath = [".", ".."]` so `backend.policy` and `app` both import.
- Verified on laptop: `/health` db ok, Neon migrated, sandbox authorize/capture/void + idempotent retry.
- `CLAUDE.md` now imports `AGENTS.md` with `@AGENTS.md` so Claude Code loads it automatically.
- New env var names: `PAYPAL_VAULT_ID`, `DATABASE_URL`, `DATABASE_URL_POOLED`.

### 2026-11-02 02:35 IST — Anurag + Claude
- Added README test-run instructions and D12 documenting exact money sums and fail-closed rounding on branch `anurag`; pushed commit `4352e5f` to `origin/anurag`.
- D12 is not yet in `main`, so the key-doc index remains D1–D11 until branch integration. No tests were rerun; this patch changes docs only.
- Files: `README.md`, `docs/DECISIONS.md`.

### 2026-11-02 02:23 IST — Anurag + Claude
- Integrated the coding-agent protocol into `AGENTS.md` and reconciled it with the direct-main rule for `CONTEXT.md` and `AGENTS.md`.
- Kept agent-specific guidance in `AGENTS.md`; `CLAUDE.md` is a pointer, while this file carries human workflow and handoff state.
- Files: `CONTEXT.md`, `AGENTS.md`, `CLAUDE.md`.

### 2026-11-02 02:18 IST — Anurag + Claude
- Separated human workflow rules in `CONTEXT.md` from agent-specific instructions in `AGENTS.md`; made `CLAUDE.md` point to `AGENTS.md`.
- Clarified that both `CONTEXT.md` and `AGENTS.md` are updated directly on `main` and code branches do not edit either file.
- Files: `CONTEXT.md`, `AGENTS.md`, `CLAUDE.md`.

### 2026-11-02 02:13 IST — Anurag + Claude
- Added rule 8: `CONTEXT.md` is updated directly on `main`; code changes stay on task branches and go through PRs.
- Updated rule 3 and aligned `AGENTS.md` and `CLAUDE.md` with the new workflow.
- Files: `CONTEXT.md`, `AGENTS.md`, `CLAUDE.md`.

### 2026-11-02 02:06 IST — Anurag + Claude
- Merged `origin/anurag` into `main`; resolved the `CONTEXT.md` conflict by retaining the task split and both branches' dated handoff entries.
- The merge brings the policy budget-rounding fix, 109 unit tests, pytest config, dev requirements, and learning notes into `main`.
- Policy merge is complete; the next shared contract task is `docs/CONTRACTS.md`.
- Files: `CONTEXT.md`, `backend/policy.py`, `backend/tests/test_policy.py`, `backend/requirements-dev.txt`, `pytest.ini`, `docs/LEARN.md`.

### 2026-11-02 02:00 IST — Anurag + Claude
- Updated `CONTEXT.md` on `main` after pulling the task-split update; retained the Sahil and Anurag handoff entries and recorded the policy tests as complete on branch `anurag`.
- Adopted the split: Anurag owns policy engine, mandates, audit log, XGBoost + SHAP, LangGraph attack agent, AG Grid console, demo video.
- New tasks go on `sahil/<task>` or `anurag/<task>` branches with a PR to `main`.
- Policy implementation and tests remain on `anurag`; merging that branch into `main` via PR is the next step.
- Files: `CONTEXT.md`.

### 2026-11-02 01:55 IST — Anurag + Claude
- Added 109 unit tests for `backend/policy.py`: every rule, boundaries (exactly at max/budget/count/velocity), malformed inputs, timezones, fail-closed on internal error, stable reason order, `PolicyDecision` invariants.
- Fixed a fail-open bug in the budget check: `budget_used + amount` used the caller's Decimal context (28 digits by default), so huge values rounded and a fully spent mandate could ALLOW. Now added in an exact context that raises on rounding -> BLOCK. Two tests cover it (both fail on the old code).
- Added `pytest.ini` (run `pytest` from repo root) and `backend/requirements-dev.txt` (pytest).
- Files: `backend/policy.py`, `backend/tests/test_policy.py`, `pytest.ini`, `backend/requirements-dev.txt`, `CONTEXT.md`, `docs/LEARN.md`.
- Branch `anurag`, not merged to `main` yet.

### 2026-11-02 01:40 IST — Sahil + Claude
- Added `task-split.md`: even split into two vertical slices (Sahil: money path + Groq judge; Anurag: trust path + XGBoost/SHAP + LangGraph agent), revised schedule from Nov 2, contract list, working rules.
- Next: both write `docs/CONTRACTS.md` today. Anurag's `backend/policy.py` stays as the policy engine; tests still needed.

### 2026-11-02 01:35 IST — Anurag + Claude
- Claimed unit tests for `backend/policy.py`; working on branch `anurag`.
- Files: `CONTEXT.md`.

### 2026-10-30 IST — Anurag + Codex
- Added `backend/policy.py`, a pure deterministic ALLOW/BLOCK evaluator with fail-closed input validation and reason codes.
- The evaluator requires trusted upstream signature verification and trusted server-side merchant/category data. Its usage snapshot is read-only; transactional reservation and MCP integration remain pending.
- No tests were added or run in this change; focused unit coverage remains next.
- Files: `backend/policy.py`, `CONTEXT.md`.

### 2026-10-30 03:45 IST — Sahil + Claude
- Added `CONTEXT.md` (this file) and `AGENTS.md` / `CLAUDE.md` pointers so every coding agent reads it first.
- Teammate joining with their own coding agent; this file is the handoff point.

### 2026-10-30 02:55 IST — Sahil + Claude
- M0 closed. Spikes 1–3 passed. Added `docs/LEARN.md` (M0 concepts + interview Qs).
- D10 (single merchant account), D11 (`mcp<2`) recorded.
- Files: `spikes/*`, `docs/DECISIONS.md`, `docs/LEARN.md`, `.env.example`.

### 2026-10-30 02:10 IST — Sahil + Claude
- Repo created; `docs/STAGE1_REQUIREMENTS.md` added. Stage 2 plan (scope, milestones, risks) agreed.
