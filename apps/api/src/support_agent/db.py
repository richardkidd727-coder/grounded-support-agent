"""Async Postgres access (SQLAlchemy 2 + asyncpg).

Accepts the plain `postgresql://...?sslmode=require` URLs that Neon/Render hand out and
translates them for asyncpg, which does not understand libpq query parameters.
"""

from __future__ import annotations

import ssl
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# libpq-only parameters that asyncpg rejects as unknown connect() kwargs.
_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding"}
_SSL_REQUIRED_MODES = {"require", "verify-ca", "verify-full"}


def to_asyncpg_url(raw_url: str) -> tuple[str, dict[str, Any]]:
    """Return an SQLAlchemy asyncpg URL plus `connect_args` derived from a libpq-style URL."""
    parts = urlsplit(raw_url)
    scheme = parts.scheme.split("+", 1)[0]
    if scheme in {"postgres", "postgresql"}:
        scheme = "postgresql+asyncpg"

    query = dict(parse_qsl(parts.query))
    connect_args: dict[str, Any] = {}
    if query.get("sslmode") in _SSL_REQUIRED_MODES:
        connect_args["ssl"] = ssl.create_default_context()
    for key in _LIBPQ_ONLY_PARAMS:
        query.pop(key, None)

    url = urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return url, connect_args


class Database:
    def __init__(self, url: str) -> None:
        async_url, connect_args = to_asyncpg_url(url)
        self.engine: AsyncEngine = create_async_engine(
            async_url,
            connect_args=connect_args,
            pool_pre_ping=True,  # free-tier Postgres (Neon) drops idle connections
            pool_size=5,
            max_overflow=5,
        )
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> AsyncSession:
        return self.sessionmaker()

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    async def dispose(self) -> None:
        await self.engine.dispose()
