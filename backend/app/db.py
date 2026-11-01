"""Async Postgres engine (Neon) and session factory."""

from collections.abc import AsyncIterator
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

# libpq options asyncpg does not understand; SSL is passed via connect_args instead.
_LIBPQ_ONLY = {"sslmode", "channel_binding"}


def to_async_url(url: str) -> tuple[str, bool]:
    """Turn a Neon/libpq URL into an asyncpg URL. Returns (url, needs_ssl)."""
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    query = dict(parse_qsl(parts.query))
    needs_ssl = query.get("sslmode", "disable") not in ("disable", "allow", "prefer")
    clean = {k: v for k, v in query.items() if k not in _LIBPQ_ONLY}
    async_url = urlunsplit((scheme, parts.netloc, parts.path, urlencode(clean), parts.fragment))
    return async_url, needs_ssl


@lru_cache
def get_engine() -> AsyncEngine:
    url, needs_ssl = to_async_url(get_settings().database_url)
    return create_async_engine(
        url,
        pool_pre_ping=True,
        connect_args={"ssl": True} if needs_ssl else {},
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


async def ping() -> bool:
    """True if the database answers SELECT 1."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
