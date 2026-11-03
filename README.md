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
6. Run the API, then open `http://127.0.0.1:8000/health` and `http://127.0.0.1:8000/audit/verify`:
   ```bash
   uv run uvicorn app.main:app --reload
   ```
7. Run the MCP server for an agent (stdio, e.g. Claude Desktop):
   ```bash
   uv run python -m app.mcp_server
   ```

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
