from fastapi.testclient import TestClient

from tests.conftest import FakeDatabase


def test_healthz_is_dependency_free(client: TestClient, fake_db: FakeDatabase) -> None:
    fake_db.healthy = False
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_readyz_ok(client: TestClient) -> None:
    res = client.get("/readyz")
    assert res.status_code == 200
    assert res.json() == {"status": "ready", "database": True, "llm_models": ["fake:fake-model"]}


def test_readyz_reports_db_outage(client: TestClient, fake_db: FakeDatabase) -> None:
    fake_db.healthy = False
    res = client.get("/readyz")
    assert res.status_code == 503
    assert res.json() == {
        "status": "degraded",
        "database": False,
        "llm_models": ["fake:fake-model"],
    }


def test_request_id_is_echoed_or_generated(client: TestClient) -> None:
    assert client.get("/healthz", headers={"X-Request-ID": "abc"}).headers["X-Request-ID"] == "abc"
    assert len(client.get("/healthz").headers["X-Request-ID"]) == 32


def test_cors_allows_configured_origin(client: TestClient) -> None:
    res = client.options(
        "/healthz",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"
