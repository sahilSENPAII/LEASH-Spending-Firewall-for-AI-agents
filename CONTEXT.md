# CONTEXT.md — shared handoff log

Two people work on this repo, each with a coding agent: **Sahil** (branch `sahil`) and
**Anurag** (branch `anurag`). This file is how each agent picks up where the other left off.

## Rules (for humans and agents)
1. **This file lives on `main` only.** Code goes on your own branch; context updates go straight to
   `main`. Never edit `CONTEXT.md` on `sahil` / `anurag` (it causes merge conflicts).
2. **Before starting work:** `git fetch && git show origin/main:CONTEXT.md` (or pull `main`), and read
   `docs/DECISIONS.md` and `task-split.md`.
3. **After every task:** update *your own* status section + add a log entry at the top of the Log, then:
   ```bash
   git switch main && git pull
   # edit CONTEXT.md
   git commit -am "context: <what you did>" && git push
   git switch <your-branch>
   ```
   Push rejected? `git pull --rebase`, then push again. If it conflicts here, keep both sides.
4. **Only edit your own status section.** The Log is shared; always add new entries at the top.
5. Every log entry says **which branch** the code is on and whether it is merged to `main`.
6. **Breaking change** to a shared function or contract (e.g. `evaluate_payment`, DB tables, decision
   format): put "⚠ BREAKING" at the start of the entry and in your status section.
7. Never put secrets here. New env var → add its *name* to `.env.example` and list it below.

## Project in one paragraph
Leash is a spending firewall for AI agents (PayPal AI Hackathon 2026, deadline Nov 13 1:30 AM IST,
we submit Nov 12 by 6 PM IST). It is an MCP server between any AI agent and PayPal. Every payment is
checked against a user-signed mandate → ALLOW / STEP_UP / BLOCK, and logged in a hash-chained audit log.
Stack: Python 3.12, FastAPI, Postgres (Neon), MCP Python SDK 1.x, PayPal REST v2 sandbox, Groq via
LiteLLM, XGBoost + SHAP, LangGraph + Playwright demo agent, React + AG Grid. Zero spend (D6).

Key docs: `task-split.md` (who owns what + schedule), `docs/STAGE1_REQUIREMENTS.md`,
`docs/DECISIONS.md` (D1–D11), `docs/LEARN.md`, `spikes/README.md`.

## Schedule (from task-split.md)
| Date | Milestone |
|---|---|
| Nov 2 | Write `docs/CONTRACTS.md` together |
| **Nov 4** | **Sync 1:** Claude Desktop pays $1 sandbox through hosted Leash (real policy, mandates, audit) |
| **Nov 6** | **Sync 2:** attack agent runs end to end and is blocked |
| **Nov 8** | **Sync 3:** judge + model in orchestrator, frontend on hosted backend |
| Nov 10 23:59 | Feature freeze |
| Nov 12 6 PM IST | Submit |

## Status — Sahil (money path + Groq judge) · branch `sahil`
- **Done (on `sahil`, not merged):** backend skeleton in `backend/` — uv project (Python 3.12),
  `app/config.py`, `app/db.py` (async SQLAlchemy + asyncpg for Neon), `app/models.py` (`payments`,
  `audit_events`), `app/state.py` (payment state machine), `app/paypal.py` (OAuth cache, authorize with
  saved wallet, capture, void, PayPal-Request-Id on every write), `app/main.py` (`/health` + DB check),
  Alembic `0001`, `scripts/smoke_paypal.py`, 22 tests, GitHub Actions CI.
- **Waiting on Sahil's laptop:** `uv run alembic upgrade head` (create tables in Neon), smoke test,
  `/health` check. (PayPal + Neon are not reachable from Claude's sandbox.)
- **Next:** MCP `request_payment` + decision orchestrator calling `backend/policy.py`, then Render deploy.
- **Heads-up for Anurag:** `audit_events` already has `prev_hash` / `hash` columns for your chain logic.
  `backend/pyproject.toml` sets pytest `testpaths = ["tests"]` (run from `backend/`); your root
  `pytest.ini` + `backend/requirements-dev.txt` need reconciling at merge — let's settle it at Sync 1.

## Status — Anurag (trust path + XGBoost/SHAP + attack agent) · branch `anurag`
- **Done:** `backend/policy.py` (on `main`). 109 unit tests + fix for a fail-open budget rounding bug
  (on `anurag`, not merged).
- **Next:** mandates (Ed25519 sign/verify, create/revoke, budget tracking), hash-chained audit log.

## Env vars (names only — values live in each person's local `.env`)
`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_BASE_URL`, `PAYPAL_VAULT_ID`,
`SANDBOX_BUYER_EMAIL`, `MERCHANT_SOUNDHUB_EMAIL`, `MERCHANT_MEGADEALS_EMAIL`,
`PLATFORM_MERCHANT_EMAIL`, `GROQ_API_KEY`, `DATABASE_URL`, `DATABASE_URL_POOLED`.
Ask Sahil for the shared sandbox values (send privately, never in the repo).

## Open items (shared)
- [ ] `docs/CONTRACTS.md` — function shapes between orchestrator (Sahil) and policy/mandates/audit/
      model (Anurag). Due today.
- [ ] Reconcile test setup (`backend/pyproject.toml` vs root `pytest.ini`) at Sync 1.
- [ ] Turn on branch protection for `main` once direct CONTEXT.md pushes are allowed for both of us.

## Log (newest first)

### 2026-11-02 02:05 IST — Sahil + Claude
- Restructured this file: it now lives on `main` only, with per-person status sections (rules above).
- Branch `sahil` (not merged): backend skeleton, Neon DB layer, payment state machine, PayPal client,
  `/health`, Alembic `0001`, smoke script, 22 tests, CI. `PAYPAL_VAULT_ID` added to `.env.example`.
- Neon project `leash` created (AWS us-east-1); Python 3.12 installed via uv.

### 2026-11-02 01:55 IST — Anurag + Claude
- Added 109 unit tests for `backend/policy.py`: every rule, boundaries, malformed inputs, timezones,
  fail-closed on internal error, stable reason order, `PolicyDecision` invariants.
- Fixed a fail-open bug in the budget check (Decimal context rounding could let a fully spent mandate
  ALLOW); now exact context, raises on rounding → BLOCK.
- Added `pytest.ini` (repo root) and `backend/requirements-dev.txt`.
- Files: `backend/policy.py`, `backend/tests/test_policy.py`, `pytest.ini`,
  `backend/requirements-dev.txt`, `docs/LEARN.md`. Branch `anurag`, not merged.

### 2026-11-02 01:40 IST — Sahil + Claude
- Added `task-split.md` (on `main`): even split into two vertical slices, schedule from Nov 2, contract
  list, working rules.

### 2026-10-30 IST — Anurag + Codex
- Added `backend/policy.py` (on `main`): pure deterministic ALLOW/BLOCK evaluator, fail-closed input
  validation, reason codes. Needs trusted signature verification + trusted merchant/category data
  upstream; usage snapshot is read-only, so atomic reservation belongs in the persistence layer.

### 2026-10-30 03:45 IST — Sahil + Claude
- Added `CONTEXT.md`, `AGENTS.md`, `CLAUDE.md` so every coding agent reads the context first.

### 2026-10-30 02:55 IST — Sahil + Claude
- M0 closed. Spikes 1–3 passed (PayPal vault/authorize/capture/void/idempotency, MCP from Claude
  Desktop, Playwright reads hidden text). D10 (single merchant account), D11 (`mcp<2`).
  `docs/LEARN.md` added.

### 2026-10-30 02:10 IST — Sahil + Claude
- Repo created; `docs/STAGE1_REQUIREMENTS.md` added. Stage 2 plan agreed.
