import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from support_agent.config import API_ROOT, Settings, get_settings
from support_agent.db import Database
from support_agent.main import create_app
from support_agent.rag.embeddings import HashingEmbedder

TEST_DB_NAME = "support_test"


class FakeDatabase(Database):
    """Database whose health is controlled by the test. The engine is never connected."""

    def __init__(self, *, healthy: bool = True) -> None:
        super().__init__("postgresql://fake:fake@localhost:1/fake")
        self.healthy = healthy

    async def ping(self) -> bool:
        return self.healthy


@pytest.fixture
def settings() -> Settings:
    return Settings(env="test", _env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def fake_db() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def client(settings: Settings, fake_db: FakeDatabase) -> Iterator[TestClient]:
    with TestClient(create_app(settings, db=fake_db, embedder=HashingEmbedder())) as c:
        yield c


# --- Postgres integration ------------------------------------------------------------------
# Uses a throwaway database on the same server as DATABASE_URL (or TEST_DATABASE_URL),
# recreated and migrated once per session. Skipped when Postgres is unreachable, unless
# REQUIRE_DB_TESTS=1 (set in CI so a broken database fails the build instead of skipping).


def _with_db_name(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


async def _recreate_database(url: str) -> None:
    admin = await asyncpg.connect(_with_db_name(url, "postgres"), timeout=5)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await admin.close()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    base = os.environ.get("TEST_DATABASE_URL") or get_settings().database_url
    url = _with_db_name(base, TEST_DB_NAME)
    try:
        asyncio.run(_recreate_database(url))
    except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
        if os.environ.get("REQUIRE_DB_TESTS") == "1":
            raise
        pytest.skip(f"Postgres not available for integration tests: {exc}")

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return url


@pytest_asyncio.fixture
async def pg_db(test_database_url: str) -> AsyncIterator[Database]:
    db = Database(test_database_url)
    async with db.engine.begin() as conn:
        await conn.execute(text("TRUNCATE kb_documents CASCADE"))
    try:
        yield db
    finally:
        await db.dispose()
