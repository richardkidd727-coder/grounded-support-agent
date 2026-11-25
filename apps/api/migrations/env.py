"""Alembic environment (async, asyncpg). Migrations are hand-written SQL - no ORM autogenerate."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from support_agent.config import get_settings
from support_agent.db import to_asyncpg_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Tests point alembic at a throwaway database via `sqlalchemy.url`; otherwise use Settings.
raw_url = config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url, connect_args = to_asyncpg_url(raw_url)
    engine = create_async_engine(url, connect_args=connect_args)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=to_asyncpg_url(raw_url)[0], literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_async_migrations())
