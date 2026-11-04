"""Leash's real MCP server on a throwaway SQLite database with a fake PayPal. Test helper only.

Started by tests/test_e2e_backend.py inside the BACKEND's environment:
  uv run --directory ../backend python tests/backend_harness.py <sqlite-file>
Everything Leash does is real (orchestrator, check_and_reserve, policy, audit chain) except the
database (SQLite, not Neon) and PayPal (a fake that authorizes and captures). Needs
MANDATE_SIGNING_KEY in the environment; it seeds m_demo with the demo terms.
"""

import asyncio
import sys
from datetime import UTC, datetime

from app import mcp_server
from app.audit import verify_chain
from app.mandates import MandateTerms, create_mandate, get_mandate
from app.models import Base
from app.paypal import Authorization
from scripts.seed_demo_mandate import DEMO_TERMS
from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles


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
