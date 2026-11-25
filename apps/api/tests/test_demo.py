"""Demo endpoints used by the web UI's persona picker and chaos menu."""

from fastapi.testclient import TestClient

from support_agent.config import Settings
from support_agent.llm.fake import FakeLlm
from support_agent.llm.router import LlmRouter
from support_agent.main import create_app
from support_agent.rag.embeddings import HashingEmbedder
from tests.conftest import FakeDatabase


def test_personas_expose_what_the_picker_needs(client: TestClient) -> None:
    personas = client.get("/api/demo/personas").json()
    assert len(personas) == 9
    alice = next(p for p in personas if p["email"] == "alice@northpeak.example")
    assert alice["card_last4"] == "4242" and alice["tier"] == "pro" and alice["try_saying"]


def test_demo_config_reports_chaos_flag(client: TestClient) -> None:
    assert client.get("/api/demo/config").json() == {"chaos_enabled": False}
    settings = Settings(_env_file=None, env="test", chaos_enabled=True)  # type: ignore[call-arg]
    app = create_app(
        settings, db=FakeDatabase(), embedder=HashingEmbedder(), agent_llm=LlmRouter([FakeLlm([])])
    )
    with TestClient(app) as chaotic:
        assert chaotic.get("/api/demo/config").json() == {"chaos_enabled": True}


def test_demo_routes_are_absent_outside_demo_mode() -> None:
    settings = Settings(_env_file=None, env="test", demo_mode=False)  # type: ignore[call-arg]
    app = create_app(
        settings, db=FakeDatabase(), embedder=HashingEmbedder(), agent_llm=LlmRouter([FakeLlm([])])
    )
    with TestClient(app) as prod:
        assert prod.get("/api/demo/personas").status_code == 404
        assert prod.get("/api/demo/config").status_code == 404
