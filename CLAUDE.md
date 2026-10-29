# Instructions for Claude Code (same as AGENTS.md)

Read `CONTEXT.md` first, every session. It holds current state, who is working on what, and the
handoff log between the two people on this project.

When you finish a meaningful change or end a session, update `CONTEXT.md` (current state, in progress,
next up, and a new log entry at the top of the Log) and commit it together with the code, then push.

Also read `docs/DECISIONS.md` before changing architecture. Do not reverse a decision there without
adding a new entry that explains why.

House rules:
- Python 3.12, FastAPI, Pydantic v2, async SQLAlchemy, Alembic. MCP Python SDK pinned `<2`.
- Zero spend: free tiers and hackathon credits only. Never add paid services.
- Secrets only in `.env` (git-ignored). New env var → add its name to `.env.example` + log it.
- Fail safe: any check error → BLOCK or STEP_UP, never ALLOW.
- Small commits with clear messages. Write tests with the code.
