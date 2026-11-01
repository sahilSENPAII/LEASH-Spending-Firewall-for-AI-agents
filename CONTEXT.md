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
   directly on `main`. Code goes on `sahil/<task>` / `anurag/<task>` branches → PR → `main`. Code
   branches do not touch `CONTEXT.md` or `AGENTS.md`.

## Project in one paragraph
Leash is a spending firewall for AI agents (PayPal AI Hackathon 2026, deadline Nov 13 1:30 AM IST,
we submit Nov 12 by 6 PM IST). It is an MCP server between any AI agent and PayPal. Every payment is
checked against a user-signed mandate → ALLOW / STEP-UP / BLOCK, and logged in a hash-chained audit log.
Stack: Python 3.12, FastAPI, Postgres (Neon), MCP Python SDK 1.x, PayPal REST v2 sandbox, Groq via
LiteLLM, XGBoost + SHAP, LangGraph + Playwright demo agent, React + AG Grid. Zero spend (see D6).

Key docs: `docs/STAGE1_REQUIREMENTS.md` (what and why), `docs/DECISIONS.md` (D1–D11),
`docs/LEARN.md` (concepts), `spikes/README.md` (how to run the M0 tests).

## Milestones
| ID | Dates | Goal | Status |
|---|---|---|---|
| M0 | Oct 30–31 | Setup + 3 de-risking spikes | **Done** |
| M1 | Oct 31–Nov 1 | Thin end-to-end flow + deploy: Claude pays $1 sandbox via hosted Leash | Next |
| M2 | Nov 2–3 | Mandates (Ed25519), policy engine, state machine, idempotency, saga, hash-chained audit | — |
| M3 | Nov 4–5 | Attack demo: SoundHub + MegaDeals stores, LangGraph agent, injection blocked | — |
| M4 | Nov 6–7 | Groq intent judge + XGBoost/SHAP anomaly model | — |
| M5 | Nov 8 | Step-up approval via web page, survives restart | — |
| M6 | Nov 9–10 | React frontend + AG Grid audit console. **Feature freeze Nov 10 23:59** | — |
| M7 | Nov 11 | Hardening, README, demo video | — |
| M8 | Nov 12 | Devpost submission | — |

## Current state
- M0 complete. Repo layout exists. `backend/policy.py` now contains a standalone deterministic evaluator; `agent/`, `stores/`, `frontend/`, and `ml/` remain placeholders.
- The policy evaluator returns ALLOW/BLOCK and relies on the caller for trusted signature verification and category/merchant provenance. It is not yet integrated with MCP or Postgres.
- Task split agreed (`task-split.md`): Sahil = money path + Groq judge; Anurag = trust path + XGBoost/SHAP + LangGraph agent.
- Workflow docs are split: human handoff rules live in `CONTEXT.md`; agent-specific rules live in `AGENTS.md`. Both are maintained directly on `main`; code goes on task branches and through PRs.
- The policy evaluator's 109 unit tests and exact Decimal budget check are now merged to `main`.
- PayPal sandbox works: Vault v3 saved wallet → AUTHORIZE (no buyer click) → capture / void,
  idempotent retries confirmed. All funds settle to the app's own merchant account (D10).
- MCP stdio server works with Claude Desktop (spike). `mcp` pinned to `<2` (D11).
- PayPal API is **not** reachable from Claude's cloud sandbox: anything calling PayPal must be run on a
  laptop.

## Env vars (names only — values live in each person's local `.env`)
`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_BASE_URL`, `SANDBOX_BUYER_EMAIL`,
`MERCHANT_SOUNDHUB_EMAIL`, `MERCHANT_MEGADEALS_EMAIL`, `PLATFORM_MERCHANT_EMAIL`, `GROQ_API_KEY`.
Coming in M1: `DATABASE_URL` (Neon). Ask Sahil for the shared sandbox values.

## In progress
- Anurag + Claude: write and agree `docs/CONTRACTS.md` (Anurag's half) with Sahil.
- Sahil + Claude: (Sahil fills in)

## Next up
- [ ] Sahil: install Python 3.12 (`brew install uv && uv python install 3.12`)
- [ ] Sahil: create Neon project `leash`, add `DATABASE_URL` to `.env`
- [x] Unit tests and budget fix for `backend/policy.py` (Anurag + Claude; merged into `main` from branch `anurag`, Nov 2).
- [ ] Both: write and agree `docs/CONTRACTS.md` (Nov 2).
- [ ] Anurag: mandates — Ed25519 sign/verify, create/revoke, reserve/release budget (branch `anurag/mandates`).
- [ ] Anurag: hash-chained audit log + `/audit/verify` (branch `anurag/audit`).
- [ ] Integrate `policy.py` into `request_payment` with trusted signature/category/merchant inputs and atomic usage reservation.
- [ ] M1 backend: FastAPI app + settings, Postgres models + Alembic, PayPal client (from spike 1),
      MCP `request_payment`, audit table, CI, Render deploy

## Log (newest first)

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
