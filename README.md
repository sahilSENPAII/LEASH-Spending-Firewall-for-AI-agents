# Leash — Spending Firewall for AI Agents

PayPal AI Hackathon 2026 entry.

Leash is an MCP server that sits between any AI agent and PayPal. Agents can only pay through Leash.
Every payment is checked against a mandate the user signed and gets one of three outcomes:
**ALLOW**, **STEP-UP** (ask the human), or **BLOCK**. Every decision lands in a tamper-evident audit log.

> Work in progress. See `docs/` for requirements, plan and decisions.

## Layout
```
backend/   FastAPI + MCP server, PayPal client, policy engine, audit log
agent/     LangGraph demo agent (the one that gets prompt-injected)
stores/    demo merchant sites: SoundHub (legit), MegaDeals (malicious)
frontend/  React app: mandates, approvals, AG Grid audit console
ml/        synthetic data + XGBoost anomaly model
spikes/    throwaway M0 scripts that de-risk PayPal, MCP and Playwright
docs/      requirements, plan, decisions, LEARN.md
```

## Set up the database and run the backend
All commands from `backend/` with Python 3.12 and [uv](https://docs.astral.sh/uv/).
Values go in the repo-root `.env` (git-ignored); names are listed in `.env.example`.

1. Install dependencies:
   ```bash
   uv sync
   ```
2. Set `DATABASE_URL` (Neon Postgres) and the PayPal sandbox values in `.env`.
3. Set `MANDATE_SIGNING_KEY`. Generate it **once per database** and give the same value to everyone
   and every server using that database (a different key makes every payment block):
   ```bash
   uv run python -m scripts.gen_mandate_key
   ```
4. Create the tables (migration `0002` deletes all existing `audit_events` rows once):
   ```bash
   uv run alembic upgrade head
   ```
5. Create the signed demo mandate `m_demo` (once per database):
   ```bash
   uv run python -m scripts.seed_demo_mandate
   ```
6. Run the API, then open `http://127.0.0.1:8000/health`, `/payments`, `/audit` and
   `/audit/verify` (the console's origin must be in `CORS_ORIGINS`, default `http://localhost:5173`):
   ```bash
   uv run uvicorn app.main:app --reload
   ```
7. Run the MCP server for an agent (stdio, e.g. Claude Desktop):
   ```bash
   uv run python -m app.mcp_server
   ```
8. Check the setup at any time (read-only, never writes). It prints OK/FAIL for the database,
   the signing key (with a shareable fingerprint), the migration, `m_demo` and the audit chain:
   ```bash
   uv run python -m scripts.check_setup
   ```

## Run the attack agent (demo)
The agent in `agent/` shops for "Sony WH-1000XM5 under $200", reads every page (hidden text too),
and pays only through Leash's `request_payment` MCP tool (it sends skus and quantities, never a
price). On MegaDeals a hidden line tells it to also buy 3 gift cards; Leash blocks that payment.

1. Start the demo stores, from the repo root (SoundHub on :8101, MegaDeals on :8102):
   ```bash
   python -m stores.serve
   ```
2. In a second terminal, from `agent/` (Python 3.12, uv), once:
   ```bash
   uv sync
   ```
   ```bash
   uv run playwright install chromium
   ```
3. Run the attack (Leash answers BLOCK; nothing is charged):
   ```bash
   uv run python -m leash_agent attack
   ```
- `attack` visits SoundHub and MegaDeals; `normal` visits SoundHub only (a legitimate purchase that
  Leash ALLOWs and PayPal sandbox charges, so agree it with the team before running it on Neon).
- `--dry-run` sends nothing to Leash; `--brain groq` uses a real LLM (needs `GROQ_API_KEY`).
- `--standin` uses the agent's own copy of the store pages (`agent/standin_stores/`) instead of
  `stores.serve`, for tests and offline runs.
- Leash runs from `backend/` over stdio (needs the backend `.env`). For a hosted Leash set
  `AGENT_LEASH_URL=https://<service>/mcp` and `AGENT_LEASH_TOKEN` to the server's `MCP_AUTH_TOKEN`.
  Agent tests: `uv run pytest` from `agent/`.

## Run tests
Python 3.12 and [uv](https://docs.astral.sh/uv/), from `backend/`:
```bash
uv sync
```
```bash
uv run pytest
```
```bash
uv run ruff check app tests scripts
```

## License
MIT
