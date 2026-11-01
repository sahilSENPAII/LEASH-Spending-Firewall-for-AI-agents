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

## Run tests
Python 3.12, from the repo root. Create and activate a virtual environment:
```bash
python -m venv .venv
```
```bash
source .venv/bin/activate
```
On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead. Then:
```bash
python -m pip install -r backend/requirements-dev.txt
```
```bash
python -m pytest
```

## License
MIT
