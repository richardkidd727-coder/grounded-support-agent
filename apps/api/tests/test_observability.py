"""Service metrics aggregated from real stored traces (Postgres), plus voice timing in traces."""

import asyncio
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from support_agent.config import Settings
from support_agent.db import Database
from support_agent.llm.fake import FakeLlm, Reply
from support_agent.llm.router import LlmRouter
from support_agent.llm.types import LlmError, LlmErrorKind
from support_agent.main import create_app
from support_agent.rag.embeddings import HashingEmbedder

pytestmark = pytest.mark.integration


@pytest.fixture
def make_client(test_database_url: str) -> Callable[..., Any]:
    async def clean() -> None:
        db = Database(test_database_url)
        async with db.engine.begin() as conn:
            await conn.execute(text("TRUNCATE chat_sessions CASCADE"))
        await db.dispose()

    asyncio.run(clean())  # aggregates are global: start from an empty trace store

    @contextmanager
    def factory(*routers: LlmRouter, **overrides: Any) -> Iterator[TestClient]:
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            env="test",
            database_url=test_database_url,
            **overrides,
        )
        app = create_app(
            settings,
            embedder=HashingEmbedder(),
            agent_llm=routers[0],
            judge_llm=routers[1] if len(routers) > 1 else LlmRouter([]),
        )
        with TestClient(app) as client:
            yield client

    return factory


def stream(client: TestClient, sid: str, message: str, **extra: Any) -> list[dict[str, Any]]:
    res = client.post("/api/chat/stream", json={"session_id": sid, "message": message, **extra})
    assert res.status_code == 200, res.text
    return [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]


def test_summary_aggregates_latency_tokens_cost_fallbacks_and_guardrails(make_client: Any) -> None:
    primary = FakeLlm(
        [
            Reply(error=LlmError(LlmErrorKind.RATE_LIMITED, "429", retry_after_s=0)),
            Reply("Hello!"),
            Reply(tool_calls=[("search_policies", {"query": "nonprofit discounts"})]),
            Reply("A specialist will follow up."),
        ],
        model="openai/gpt-oss-120b",
    )
    backup = FakeLlm([Reply("From the backup.")], model="qwen/qwen3.8-27b")
    with make_client(LlmRouter([primary, backup])) as client:
        sid = client.post("/api/sessions").json()["session_id"]
        stream(client, sid, "hi")  # primary 429 -> backup answers
        stream(client, sid, "hello again")
        # empty knowledge base in the test DB -> reject band -> guardrail opens a ticket
        events = stream(
            client,
            sid,
            "Do you offer nonprofit discounts?",
            mode="voice",
            voice_input={"stt_model": "whisper-large-v3-turbo", "stt_ms": 640, "audio_s": 2.4},
        )
        started = events[0]
        assert started["voice_input"] == {
            "stt_model": "whisper-large-v3-turbo",
            "stt_ms": 640,
            "audio_s": 2.4,
        }

        summary = client.get("/api/observability/summary").json()

    assert (summary["sessions"], summary["turns"], summary["voice_turns"]) == (1, 3, 1)
    assert summary["outcomes"] == {"answered": 2, "escalated": 1, "degraded": 0}
    assert summary["model_fallbacks"] == 1
    assert summary["total_ms"]["p50"] is not None and summary["ttft_ms"]["p95"] is not None
    assert summary["input_tokens"] == 4 * 100 and summary["output_tokens"] == 4 * 20
    calls = {m["model"]: m["calls"] for m in summary["models"]}
    assert calls == {"openai/gpt-oss-120b": 3, "qwen/qwen3.8-27b": 1}
    assert summary["cost_usd_list_price"] > 0
    assert summary["cost_usd_per_1k_turns"] == pytest.approx(
        summary["cost_usd_list_price"] / 3 * 1000, rel=1e-3
    )
    assert summary["guardrails"] == {
        "answered": 0,
        "escalated": 1,
        "judge_calls": 0,
        "auto_tickets": 1,
    }
    steps = {s["name"]: s for s in summary["steps"]}
    assert steps["search_policies"]["calls"] == 1
    assert steps["create_escalation_ticket"]["errors"] == 0
    # aggregates only: nothing the customer said leaks into the operator view
    assert "nonprofit" not in json.dumps(summary)


def test_empty_window(make_client: Any) -> None:
    with make_client(LlmRouter([FakeLlm([])])) as client:
        summary = client.get("/api/observability/summary?window_hours=1").json()
    assert summary["turns"] == 0 and summary["cost_usd_per_1k_turns"] is None
    assert summary["total_ms"] == {"p50": None, "p95": None}


def test_voice_input_is_ignored_for_chat_turns(make_client: Any) -> None:
    with make_client(LlmRouter([FakeLlm([Reply("ok")])])) as client:
        sid = client.post("/api/sessions").json()["session_id"]
        events = stream(
            client, sid, "typed", voice_input={"stt_model": "x", "stt_ms": 1, "audio_s": 1}
        )
    assert events[0]["voice_input"] is None


def test_token_protects_the_operator_view(make_client: Any) -> None:
    with make_client(LlmRouter([FakeLlm([])]), observability_token="s3cret") as client:
        assert client.get("/api/observability/summary").status_code == 401
        wrong = {"Authorization": "Bearer nope"}
        assert client.get("/api/observability/summary", headers=wrong).status_code == 401
        ok = {"Authorization": "Bearer s3cret"}
        assert client.get("/api/observability/summary", headers=ok).status_code == 200
