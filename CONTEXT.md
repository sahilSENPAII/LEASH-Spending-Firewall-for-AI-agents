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
   anything the other side must know (new env vars, migrations, broken things).
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

Key docs: `docs/STAGE1_REQUIREMENTS.md` (what and why), `docs/DECISIONS.md` (D1–D13),
`docs/LEARN.md` (concepts), `spikes/README.md` (how to run the M0 tests).

## Milestones
| ID | Dates | Goal | Status |
|---|---|---|---|
| M0 | Oct 30–31 | Setup + 3 de-risking spikes | **Done** |
| M1 | Oct 31–Nov 1 | Thin end-to-end flow + deploy: Claude pays $1 sandbox via hosted Leash | Local flow **done** (Claude Desktop paid $1 sandbox through the orchestrator); hosted deploy pending (Sahil, Render) |
| M2 | Nov 2–3 | Mandates (Ed25519), policy engine, state machine, idempotency, saga, hash-chained audit | **Mostly done**: policy, signed mandates, budget holds, audit chain (Anurag); state machine, idempotency keys, void on capture failure (Sahil). Pending: orchestrator switch to `check_and_reserve` / `release_budget` / `append_audit`, migration `0002` on Neon |
| M3 | Nov 4–5 | Attack demo: SoundHub + MegaDeals stores, LangGraph agent, injection blocked | — |
| M4 | Nov 6–7 | Groq intent judge + XGBoost/SHAP anomaly model | — |
| M5 | Nov 8 | Step-up approval via web page, survives restart | — |
| M6 | Nov 9–10 | React frontend + AG Grid audit console. **Feature freeze Nov 10 23:59** | — |
| M7 | Nov 11 | Hardening, README, demo video | — |
| M8 | Nov 12 | Devpost submission | — |

## Current state
- M0 complete. Repo layout exists. `backend/app/policy.py` now contains a standalone deterministic evaluator; `agent/`, `stores/`, `frontend/`, and `ml/` remain placeholders.
- The policy evaluator is integrated into `backend/app/orchestrator.py`, which persists payment and audit state in Postgres. The main orchestrator still uses hardcoded `DEMO_MANDATE` and an unlocked `_usage()` query. `check_and_reserve` / `release_budget` are on `main` (`f3de389`, merged at `f50199f`) but are not wired into the payment flow yet.
- Mandates part 1: `app/canonical.py` (canonical JSON) and `app/mandates.py` (`MandateTerms` including signed `intent`, `MandateRecord` with `budget_used`/`payment_count`, Ed25519 sign/verify, and fail-closed `verify_mandate`). The 202 backend tests pass.
- Mandates part 2 is implemented and pushed on `anurag` (`f3de389`, merged into `main` at `f50199f`): `mandates` and `budget_holds` tables, mandate CRUD, atomic budget holds, and velocity counting. The 235-test suite passes.
- Audit chain is implemented in `backend/app/audit.py`: `append_audit` links entries with SHA-256 over the previous hash and canonical JSON under a Postgres advisory lock; `verify_chain` returns `ChainCheck`, and `GET /audit/verify` is available. Mandate create/revoke write audit events, and mandate, hold, and audit timestamps are stored in UTC. D13 records the chain rules and tail-truncation limit. The 271-test suite passes.
- Before any payment on Neon after migration `0002` and the `m_demo` seed, Sahil must route every audit write through `append_audit`. `backend/app/orchestrator.py` still writes `AuditEvent(...)` directly; those unhashed rows break `/audit/verify` and make the next append fail closed.
- Task split agreed (`task-split.md`): Sahil = money path + Groq judge; Anurag = trust path + XGBoost/SHAP + LangGraph agent.
- Workflow docs are split: human handoff rules live in `CONTEXT.md`; agent-specific rules live in `AGENTS.md`. Both are maintained directly on `main`; code goes on task branches and through PRs.
- The policy evaluator's 109 unit tests and exact Decimal budget check are now merged to `main`.
- `backend/tests/test_policy.py` passes Sahil's Ruff 0.17.3 CI rules; the lint fix is merged to `main`.
- README test instructions and D12's exact-budget decision are now merged into `main` from `anurag`.
- Contracts agreed (Nov 2): Sahil said yes to all 4 open questions and the `check_and_reserve` design. Section 5 and `payment.blocked` are in `docs/CONTRACTS.md`; Sahil updated the app paths and renamed `payment.error` to `payment.failed`. The `void_failed` audit payload remains pending.
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
  has database setup steps. 274 tests pass.

## Env vars (names only — values live in each person's local `.env`)
`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_BASE_URL`, `SANDBOX_BUYER_EMAIL`,
`MERCHANT_SOUNDHUB_EMAIL`, `MERCHANT_MEGADEALS_EMAIL`, `PLATFORM_MERCHANT_EMAIL`, `GROQ_API_KEY`,
`PAYPAL_VAULT_ID` (saved demo-buyer wallet token), `DATABASE_URL`, `DATABASE_URL_POOLED` (Neon),
`MANDATE_SIGNING_KEY` (base64 Ed25519 private key; generate once per database with `uv run python -m scripts.gen_mandate_key` from `backend/`, then provision the same key securely for Sahil, Anurag, and Render; a different key makes signature verification fail and blocks payments),
`VELOCITY_MAX_PAYMENTS` (default 3), `VELOCITY_WINDOW_SECONDS` (default 600).
Ask Sahil for the shared sandbox values.

## In progress
- Anurag + Codex: LangGraph attack agent (Nov 4–6), waiting on Sahil's answers about the demo stores, MCP transport, and `request_payment` arguments.
- Sahil + Claude: paused (back later on Nov 2). Next: Groq intent judge (`app/judge.py`), HTTP MCP
  transport + `list_mandates` / `get_payment_status`, Render deploy ($50 credits claimed, no card).

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
- [ ] Both: agree on one shared `MANDATE_SIGNING_KEY` for the Neon database and choose who runs migration `0002`.
- [ ] Sahil: after key agreement, run migration `0002` and seed `m_demo` on Neon. Migration `0002` deletes every `audit_events` row; it leaves `payments` untouched. Do not run it until the key and runner are agreed.
- [ ] Sahil (before any payment on Neon after the seed): switch the orchestrator to `check_and_reserve` / `release_budget` / `append_audit` per `docs/CONTRACTS.md` section 5.3; replace every direct `AuditEvent(...)` write (`payment.decided`, `_move`, `_fail`) with `append_audit(session, event_type=..., payment_id=..., payload=..., now=now)`; drop `DEMO_MANDATE` and `_usage()`.
- [ ] Sahil (minor): fix the three `0001` nullable mismatches: `payments.created_at`, `payments.updated_at`, and `audit_events.created_at`.
- [x] Sahil: update the `docs/CONTRACTS.md` app paths.
- [x] Sahil: rename `payment.error` to `payment.failed`.
- [x] M1 backend skeleton: FastAPI app + settings, Postgres models + Alembic, PayPal client, CI
      (merged into `main` from `sahil`).
- [x] Sahil: MCP `request_payment` + orchestrator (merged into `main` from `sahil`).
- [ ] Sahil: MCP `list_mandates` / `get_payment_status`.
- [ ] Sahil: Render deploy.
- [ ] Anurag: LangGraph attack agent (Nov 4–6).

## Log (newest first)

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
