# Instructions for coding agents

This file is the source of agent-specific repository rules. `CLAUDE.md` is an entry point to this file. Do not duplicate these rules in `CONTEXT.md` or `CLAUDE.md`.

## Session start
- Pull the current branch before work.
- Read `CONTEXT.md` and `docs/DECISIONS.md` at the start of each session. `CONTEXT.md` contains the human-maintained project status, handoff, and task ownership.
- Read `docs/DECISIONS.md` before architecture changes. Do not reverse a decision without recording why.

## Project handoff and files
- Keep agent-specific instructions in this file; keep human workflow and project handoff in `CONTEXT.md`.
- After meaningful work or at session end, update `CONTEXT.md` directly on `main` with claims, current state, in-progress work, next steps, and a top-of-log entry; push it to `main` per `CONTEXT.md` rule 8.
- When agent instructions change, update `AGENTS.md` directly on `main` and record the change in `CONTEXT.md`.
- Do not edit `CONTEXT.md` or `AGENTS.md` on a code branch. Code goes on `sahil/<task>` or `anurag/<task>` branches and is submitted through a PR to `main`.

## Coding rules
- Python 3.12, FastAPI, Pydantic v2, async SQLAlchemy, Alembic. MCP Python SDK pinned `<2`.
- Zero spend: free tiers and hackathon credits only. Never add paid services.
- Secrets only in `.env` (git-ignored). New environment variables must be named in `.env.example` and recorded in `CONTEXT.md`.
- Fail safe: any check error → BLOCK or STEP_UP, never ALLOW.
- Keep code commits small and clear. Write tests with the code; submit code changes through a task-branch PR, not a direct push to `main`.
