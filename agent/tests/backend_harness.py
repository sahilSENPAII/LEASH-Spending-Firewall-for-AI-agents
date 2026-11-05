"""Leash's real MCP server on throwaway SQLite with fake PayPal and judge. Test helper only.

Started by tests/test_e2e_backend.py inside the BACKEND's environment:
  uv run --directory ../backend python tests/backend_harness.py <sqlite-file>
Everything Leash does is real (orchestrator, check_and_reserve, policy, audit chain) except the
database (SQLite, not Neon), PayPal (a fake that authorizes and captures), the intent judge (a fake
that says the cart matches, so no GROQ_API_KEY is needed) and the clock (pinned to FIXED_NOW).
Needs MANDATE_SIGNING_KEY in the environment; it seeds m_demo with the demo terms.
"""

import asyncio
import sys
from datetime import UTC, datetime

from app import mcp_server, orchestrator
from app.audit import verify_chain
from app.mandates import MandateTerms, create_mandate, get_mandate
from app.models import Base
from app.paypal import Authorization
from scripts.seed_demo_mandate import DEMO_TERMS
from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

# Every payment in the test happens at this fixed daytime instant (14:30 IST), so the result does
# not depend on when the test runs (the anomaly model looks at the hour of day).
FIXED_NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
_request_payment = orchestrator.request_payment


async def _request_payment_at_fixed_time(*args, **kwargs):
    kwargs.setdefault("now", FIXED_NOW)
    return await _request_payment(*args, **kwargs)


orchestrator.request_payment = _request_payment_at_fixed_time


@compiles(BigInteger, "sqlite")
def _sqlite_bigint(type_, compiler, **kw):
    return "INTEGER"


class FakePayPal:
    async def authorize(self, **kwargs):
        return Authorization(order_id="O-test", authorization_id="A-test", status="CREATED")

    async def capture(self, authorization_id, request_id):
        return "COMPLETED"

    async def void(self, authorization_id, request_id):
        return None


async def _judge_says_match(**kwargs):
    from app.judge import JudgeResult

    return JudgeResult(match=True, score=0.05, reason="ok", model="test")


# Without a Groq key the real judge fails, and a failed check is a STEP_UP (D3).
if hasattr(orchestrator, "judge_intent"):
    orchestrator.judge_intent = _judge_says_match


async def prepare(path: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as s:
        if await get_mandate(s, "m_demo") is None:
            await create_mandate(s, MandateTerms(**DEMO_TERMS, created_at=datetime.now(UTC)))
            await s.commit()
    return sessions


async def chain_ok(sessions) -> bool:
    async with sessions() as s:
        return (await verify_chain(s)).ok


if __name__ == "__main__":
    sessions = asyncio.run(prepare(sys.argv[1]))
    mcp_server.get_sessionmaker = lambda: sessions
    mcp_server.get_paypal = lambda: FakePayPal()
    if "--check-chain" in sys.argv:
        print("chain ok" if asyncio.run(chain_ok(sessions)) else "chain BROKEN")
    else:
        mcp_server.mcp.run()
