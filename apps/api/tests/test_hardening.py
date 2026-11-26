"""Abuse limits, security headers and failure shaping (no database needed)."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_agent.config import Settings
from support_agent.llm.fake import FakeLlm
from support_agent.llm.router import LlmRouter
from support_agent.main import create_app
from support_agent.protection import TokenBucket
from support_agent.rag.embeddings import HashingEmbedder
from tests.conftest import FakeDatabase

SESSION = "sess_" + "a" * 24


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def make_client() -> Iterator[Any]:
    clients: list[TestClient] = []

    def factory(**overrides: Any) -> TestClient:
        settings = Settings(_env_file=None, **{"env": "test", **overrides})  # type: ignore[call-arg]
        app = create_app(
            settings,
            db=FakeDatabase(),  # unreachable Postgres: every DB call fails
            embedder=HashingEmbedder(),
            agent_llm=LlmRouter([FakeLlm([])]),
        )
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


# --- token bucket ---------------------------------------------------------------------------


def test_bucket_allows_a_burst_then_refills_at_the_rate() -> None:
    clock = Clock()
    bucket = TokenBucket(60, burst=3, clock=clock)  # 1 token/s
    assert [bucket.take("ip") for _ in range(3)] == [None, None, None]
    assert bucket.take("ip") == pytest.approx(1.0)
    clock.now = 2.5
    assert bucket.take("ip") is None and bucket.take("ip") is None
    assert bucket.take("ip") == pytest.approx(0.5)
    assert bucket.take("other-ip") is None  # buckets are per key


def test_bucket_memory_is_bounded() -> None:
    bucket = TokenBucket(60, max_keys=100, clock=Clock())
    for i in range(1_000):
        bucket.take(f"ip-{i}")
    assert len(bucket._buckets) == 100


# --- limits on the API -----------------------------------------------------------------------


def test_new_conversations_are_rate_limited_per_ip(make_client: Any) -> None:
    client = make_client(ip_sessions_per_minute=2)
    first = [client.post("/api/sessions").status_code for _ in range(2)]
    assert first == [503, 503]  # passed the limiter, then hit the (down) database
    limited = client.post("/api/sessions")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    assert "new conversations" in limited.json()["detail"]


def test_turns_are_rate_limited_per_session_with_a_small_burst(make_client: Any) -> None:
    client = make_client(rate_limit_per_minute=4)  # burst = max(3, 4/4) = 3
    body = {"session_id": SESSION, "message": "hi"}
    codes = [client.post("/api/chat/stream", json=body).status_code for _ in range(4)]
    assert codes == [503, 503, 503, 429]
    other = {"session_id": "sess_" + "b" * 24, "message": "hi"}
    assert client.post("/api/chat/stream", json=other).status_code == 503  # not limited


def test_conversation_length_is_capped(make_client: Any) -> None:
    client = make_client(session_max_turns=2)
    body = {"session_id": SESSION, "message": "hi"}
    for _ in range(2):
        client.post("/api/chat/stream", json=body)
    res = client.post("/api/chat/stream", json=body)
    assert res.status_code == 422 and "length limit" in res.json()["detail"]


def test_oversized_messages_are_rejected(make_client: Any) -> None:
    client = make_client()
    res = client.post("/api/chat/stream", json={"session_id": SESSION, "message": "x" * 2001})
    assert res.status_code == 422


# --- headers and error shaping ---------------------------------------------------------------


def test_security_headers(make_client: Any) -> None:
    client = make_client()
    res = client.get("/api/demo/personas")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["Content-Security-Policy"].startswith("default-src 'none'")
    assert res.headers["Cache-Control"] == "no-store"
    assert "Strict-Transport-Security" not in res.headers  # only in production
    docs = client.get("/docs")
    assert "Content-Security-Policy" not in docs.headers  # Swagger UI needs its scripts
    prod = make_client(env="production")
    assert "max-age" in prod.get("/healthz").headers["Strict-Transport-Security"]


def test_untrusted_request_ids_are_replaced(make_client: Any) -> None:
    client = make_client()
    assert (
        client.get("/healthz", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"]
        == "abc-123"
    )
    forged = client.get("/healthz", headers={"X-Request-ID": "x\nlevel=error " + "a" * 200})
    assert len(forged.headers["X-Request-ID"]) == 32


def test_database_outage_is_a_retryable_503(make_client: Any) -> None:
    res = make_client().post("/api/sessions")
    assert res.status_code == 503
    assert res.headers["Retry-After"] == "5"
    assert res.json() == {"detail": "The service is temporarily unavailable. Please retry shortly."}


def test_unexpected_errors_never_leak_internals(make_client: Any) -> None:
    client = make_client()

    async def boom() -> str:
        raise RuntimeError("secret internal detail at /srv/app.py line 42")

    client.app.state.runner.store.create = boom  # type: ignore[attr-defined]
    res = client.post("/api/sessions")
    assert res.status_code == 500
    body = res.json()
    assert body["detail"] == "Internal error." and len(body["request_id"]) == 32
    assert "secret" not in res.text and "Traceback" not in res.text


def test_cors_only_allows_the_headers_the_client_sends(make_client: Any) -> None:
    client = make_client()
    ok = client.options(
        "/api/chat/stream",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-chaos",
        },
    )
    assert ok.status_code == 200
    bad = client.options(
        "/api/chat/stream",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-evil",
        },
    )
    assert bad.status_code == 400
    foreign = client.options(
        "/api/chat/stream",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in foreign.headers
