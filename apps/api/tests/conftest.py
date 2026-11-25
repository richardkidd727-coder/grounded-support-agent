from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from support_agent.config import Settings
from support_agent.db import Database
from support_agent.main import create_app


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
    with TestClient(create_app(settings, db=fake_db)) as c:
        yield c
