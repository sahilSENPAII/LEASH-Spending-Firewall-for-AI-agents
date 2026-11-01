# CONTEXT.md — shared handoff log

Two people (Sahil + teammate) and their coding agents work on this repo.
This file is how each agent picks up exactly where the other left off. **Keep it current.**

## Rules (for humans and agents)
1. **Before starting work:** `git pull`, then read this whole file plus `docs/DECISIONS.md`.
2. **Claim work:** move the task to "In progress" with your name before you start, commit + push that
   change right away so the other side doesn't pick the same task.
3. **After every meaningful change** (a feature, fix, decision, or end of a session): update
   "Current state", "In progress", "Next up", add a log entry at the **top** of the Log, then commit
   and push in the same commit as the code.
4. Log entries: date (IST), who (person + agent), what changed, files touched, anything left half-done,
   anything the other side must know (new env vars, migrations, broken things).
5. Decisions with trade-offs go in `docs/DECISIONS.md`; concepts and interview notes in `docs/LEARN.md`.
6. Never put secrets here. New env vars: add the *name* to `.env.example` and mention it in the log.
7. Small commits, push often. If `git pull` conflicts in this file, keep both sides' entries.

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
- PayPal sandbox works: Vault v3 saved wallet → AUTHORIZE (no buyer click) → capture / void,
  idempotent retries confirmed. All funds settle to the app's own merchant account (D10).
- MCP stdio server works with Claude Desktop (spike). `mcp` pinned to `<2` (D11).
- PayPal API is **not** reachable from Claude's cloud sandbox: anything calling PayPal must be run on a
  laptop.

## Env vars (names only — values live in each person's local `.env`)
`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_BASE_URL`, `SANDBOX_BUYER_EMAIL`,
`MERCHANT_SOUNDHUB_EMAIL`, `MERCHANT_MEGADEALS_EMAIL`, `PLATFORM_MERCHANT_EMAIL`, `GROQ_API_KEY`.
`DATABASE_URL`, `DATABASE_URL_POOLED` (Neon), `PAYPAL_VAULT_ID` (saved demo-buyer wallet token).
Ask Sahil for the shared sandbox values.

## In progress
- Sahil: working directly on `main`; task scope has not been recorded in this handoff yet.

## Next up
- [ ] Add focused unit coverage for `backend/policy.py`, then integrate it into `request_payment` with trusted signature/category/merchant inputs and atomic usage reservation.
- [ ] M1 backend: FastAPI app + settings, Postgres models + Alembic, PayPal client (from spike 1),
      MCP `request_payment`, audit table, CI, Render deploy

## Log (newest first)

### 2026-11-02 02:00 IST — Sahil + Claude (branch `sahil`)
- Backend skeleton in `backend/` (uv project, Python 3.12): `app/config.py` (settings from root `.env`),
  `app/db.py` (async SQLAlchemy + asyncpg for Neon; strips libpq-only URL params), `app/models.py`
  (`payments`, `audit_events` — audit has `prev_hash`/`hash` columns ready for Anurag's chain logic),
  `app/state.py` (payment state machine), `app/paypal.py` (OAuth cache, authorize with vault token,
  capture, void, PayPal-Request-Id on every write), `app/main.py` (`/health` with DB check).
- Alembic migration `0001` creates `payments` + `audit_events`. Not yet applied to Neon (run on a laptop).
- 22 tests passing (state machine, PayPal client mocked with respx, DB URL, health). CI workflow added.
- `backend/policy.py` untouched; CI lint covers `app tests scripts` only for now.
- How to run (from `backend/`): `uv sync`, `uv run pytest`, `uv run alembic upgrade head`,
  `uv run uvicorn app.main:app --reload`, `uv run python scripts/smoke_paypal.py`.

### 2026-11-02 01:40 IST — Sahil + Claude
- Added `task-split.md`: even split into two vertical slices (Sahil: money path + Groq judge; Anurag: trust path + XGBoost/SHAP + LangGraph agent), revised schedule from Nov 2, contract list, working rules.
- Next: both write `docs/CONTRACTS.md` today. Anurag's `backend/policy.py` stays as the policy engine; tests still needed.

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
