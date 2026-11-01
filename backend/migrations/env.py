"""Alembic env: async migrations against DATABASE_URL from the repo-root .env."""

import asyncio
from logging.config import fileConfig

from alembic import context

from app.db import get_engine
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_offline() -> None:
    raise SystemExit("offline mode not supported; run against a database")


def _do_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    async with get_engine().connect() as conn:
        await conn.run_sync(_do_run)


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
