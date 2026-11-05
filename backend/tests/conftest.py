"""Shared test setup: an in-memory SQLite database with every table from app.models, and no LLM."""

import httpx
import pytest
from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app import judge
from app.models import Base


# SQLite only auto-increments INTEGER PRIMARY KEY, so audit_events.id needs INTEGER here.
@compiles(BigInteger, "sqlite")
def _sqlite_bigint(type_, compiler, **kw):
    return "INTEGER"


@pytest.fixture(autouse=True)
def _no_llm_calls(monkeypatch):
    # No test reaches Groq; a test that needs the judge fakes judge_intent or judge._client.
    def refuse(request):
        raise RuntimeError("tests must not call the LLM")

    monkeypatch.setattr(
        judge, "_client", lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    )


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
