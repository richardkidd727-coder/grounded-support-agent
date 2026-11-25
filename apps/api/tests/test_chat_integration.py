"""Sessions, the turn runner and the SSE endpoint against real Postgres (scripted models)."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_agent.agent.grounding import GroundingJudge
from support_agent.agent.orchestrator import Agent
from support_agent.agent.runner import SessionBusy, TurnRunner
from support_agent.agent.store import SessionNotFound, SessionStore
from support_agent.config import Settings
from support_agent.crm.seed import seed_personas
from support_agent.db import Database
from support_agent.events.schema import AgentEvent
from support_agent.llm.fake import FakeLlm, Reply
from support_agent.llm.router import LlmRouter
from support_agent.llm.types import LlmRequest, LlmResult, StreamItem, Usage
from support_agent.main import create_app
from support_agent.rag.embeddings import HashingEmbedder
from support_agent.tools.base import ToolDeps, utcnow
from support_agent.tools.registry import build_executor

pytestmark = pytest.mark.integration

VERIFY = ("verify_identity", {"email": "alice@northpeak.example", "card_last4": "4242"})


def parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for frame in body.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in frame.splitlines() if line and not line.startswith(":")
        )
        if "data" in fields:
            event = json.loads(fields["data"])
            assert fields["event"] == event["type"] and int(fields["id"]) == event["seq"]
            events.append(event)
    return events


# --- HTTP ----------------------------------------------------------------------------------------


@pytest.fixture
def make_client(test_database_url: str) -> Callable[..., Any]:
    async def seed() -> None:
        db = Database(test_database_url)
        await seed_personas(db)
        await db.dispose()

    asyncio.run(seed())

    @contextmanager
    def factory(script: list[Any], **overrides: Any) -> Iterator[tuple[TestClient, FakeLlm]]:
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            env="test",
            database_url=test_database_url,
            **overrides,
        )
        llm = FakeLlm(script)
        app = create_app(
            settings,
            embedder=HashingEmbedder(),
            agent_llm=LlmRouter([llm]),
            judge_llm=LlmRouter([FakeLlm([])]),
        )
        with TestClient(app) as client:
            yield client, llm

    return factory


def test_conversation_over_sse_remembers_identity_and_history(make_client: Any) -> None:
    script = [
        Reply("Let me verify you.", tool_calls=[VERIFY]),
        Reply("You're verified, Alice."),
        Reply("Your plan is Pro."),
    ]
    with make_client(script) as (client, llm):
        created = client.post("/api/sessions")
        assert created.status_code == 201
        sid = created.json()["session_id"]

        res = client.post("/api/chat/stream", json={"session_id": sid, "message": "hi, it's me"})
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(res.text)
        assert events[0]["type"] == "turn.started" and events[-1]["type"] == "turn.completed"
        assert [e["seq"] for e in events] == list(range(len(events)))
        assert events[-1]["final_text"] == "Let me verify you.\n\nYou're verified, Alice."

        # second turn, by voice: same session, so identity and history carry over
        res = client.post(
            "/api/chat/stream",
            json={"session_id": sid, "message": "what plan am I on?", "mode": "voice"},
        )
        assert parse_sse(res.text)[-1]["final_text"] == "Your plan is Pro."
        third_request = llm.requests[2]
        assert "verified as Alice Martin (pro plan)" in third_request.messages[0].content
        assert any(m.content == "hi, it's me" for m in third_request.messages)
        assert "issue_refund" in {t["name"] for t in third_request.tools}

        view = client.get(f"/api/sessions/{sid}").json()
        assert view["customer"] == {"name": "Alice Martin", "tier": "pro"}
        assert view["turn_count"] == 2
        assert [(m["role"], m["mode"]) for m in view["messages"]] == [
            ("user", "chat"),
            ("assistant", "chat"),
            ("assistant", "chat"),
            ("user", "voice"),
            ("assistant", "voice"),
        ]

        turns = client.get(f"/api/sessions/{sid}/turns").json()
        assert [t["mode"] for t in turns] == ["voice", "chat"]  # newest first
        assert turns[1]["events"][0]["type"] == "turn.started"
        assert turns[1]["llm_calls"] == 2 and turns[1]["outcome"] == "answered"


def test_request_errors(make_client: Any) -> None:
    with make_client([]) as (client, _):
        missing = "sess_" + "x" * 24
        res = client.post("/api/chat/stream", json={"session_id": missing, "message": "hi"})
        assert res.status_code == 404
        assert client.get(f"/api/sessions/{missing}").status_code == 404
        res = client.post("/api/chat/stream", json={"session_id": "nope", "message": "hi"})
        assert res.status_code == 422
        sid = client.post("/api/sessions").json()["session_id"]
        res = client.post("/api/chat/stream", json={"session_id": sid, "message": ""})
        assert res.status_code == 422
        res = client.post(
            "/api/chat/stream",
            json={"session_id": sid, "message": "hi"},
            headers={"X-Chaos": "llm:timeout"},
        )
        assert res.status_code == 400  # chaos is disabled by default


def test_chaos_header_injects_faults_when_enabled(make_client: Any) -> None:
    with make_client([Reply("never reached")], chaos_enabled=True) as (client, llm):
        sid = client.post("/api/sessions").json()["session_id"]
        bad = client.post(
            "/api/chat/stream",
            json={"session_id": sid, "message": "hi"},
            headers={"X-Chaos": "nonsense"},
        )
        assert bad.status_code == 400
        res = client.post(
            "/api/chat/stream",
            json={"session_id": sid, "message": "hi"},
            headers={"X-Chaos": "llm:timeout"},
        )
        events = parse_sse(res.text)
        assert [e["code"] for e in events if e["type"] == "error"] == ["llm_timeout"]
        assert events[-1]["outcome"] == "degraded"
        assert llm.requests == []


# --- runner & store ----------------------------------------------------------------------------


class GatedLlm:
    """Holds its reply until the test opens the gate."""

    provider = "fake"
    model = "gated"

    def __init__(self) -> None:
        self.gate = asyncio.Event()

    async def stream(self, request: LlmRequest) -> AsyncIterator[StreamItem]:
        await self.gate.wait()
        yield LlmResult("fake", "gated", "done", (), Usage(), "stop", 1)


@pytest.fixture
async def runner_parts(pg_db: Database) -> tuple[TurnRunner, SessionStore, GatedLlm]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    llm = GatedLlm()
    agent = Agent(
        llm=LlmRouter([llm]),
        judge=GroundingJudge(LlmRouter([])),
        tools=build_executor(),
        deps=ToolDeps(pg_db, None, settings),  # type: ignore[arg-type]
        settings=settings,
    )
    store = SessionStore(pg_db)
    return TurnRunner(agent, store), store, llm


async def drain(events: AsyncIterator[AgentEvent | None]) -> list[AgentEvent]:
    return [e async for e in events if e is not None]


async def test_one_turn_at_a_time_per_session(
    runner_parts: tuple[TurnRunner, SessionStore, GatedLlm],
) -> None:
    runner, store, llm = runner_parts
    sid = await store.create()
    first = await runner.start(sid, "first")
    with pytest.raises(SessionBusy):
        await runner.start(sid, "second")
    other = await store.create()  # other sessions are unaffected
    llm.gate.set()
    await drain(await runner.start(other, "hello"))
    assert (await drain(first))[-1].type == "turn.completed"
    await drain(await runner.start(sid, "third"))  # free again once the turn ends


async def test_a_turn_finishes_and_is_saved_even_if_nobody_listens(
    runner_parts: tuple[TurnRunner, SessionStore, GatedLlm],
) -> None:
    runner, store, llm = runner_parts
    sid = await store.create()
    await runner.start(sid, "refund me")  # the client disconnects immediately
    llm.gate.set()
    await runner.shutdown(grace_s=5)
    state = await store.load(sid)
    assert [m.message.content for m in state.history] == ["refund me", "done"]
    assert state.turn_count == 1


async def test_heartbeats_while_a_turn_is_slow(
    runner_parts: tuple[TurnRunner, SessionStore, GatedLlm],
) -> None:
    runner, store, llm = runner_parts
    sid = await store.create()
    events = await runner.start(sid, "hi", heartbeat_s=0.01)
    seen: list[AgentEvent | None] = []
    async for item in events:
        seen.append(item)
        if item is None:  # the model is stuck: a keep-alive arrives instead of silence
            llm.gate.set()
    assert None in seen
    real = [e for e in seen if e is not None]
    assert real[0].type == "turn.started" and real[-1].type == "turn.completed"


async def test_sessions_expire_after_inactivity(pg_db: Database) -> None:
    now = utcnow()
    clock = {"now": now}
    store = SessionStore(pg_db, ttl_hours=24, clock=lambda: clock["now"])
    sid = await store.create()
    await store.load(sid)
    clock["now"] = now + timedelta(hours=25)
    with pytest.raises(SessionNotFound):
        await store.load(sid)
    with pytest.raises(SessionNotFound):
        await store.load("sess_does_not_exist_000000")
