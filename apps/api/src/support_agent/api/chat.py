"""Chat endpoints: sessions, the streaming turn endpoint (SSE), transcript and turn traces.

Streaming protocol: `POST /api/chat/stream` answers `text/event-stream`. Each frame is

    id: <seq>
    event: <event type, e.g. step.started>
    data: <the event as JSON - see events/schema.py>

plus `: keep-alive` comments while a slow tool or model is working. POST (not EventSource/GET)
because the message is a body, not a URL parameter; the browser reads it with fetch + a stream
reader. The turn runs server-side regardless of the connection, and the full event log is
persisted, so a client that drops can recover the result from `GET /api/sessions/{id}`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Path, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from support_agent.agent.runner import SessionBusy, TurnRunner
from support_agent.agent.store import SESSION_ID_PATTERN, SessionNotFound
from support_agent.config import Settings
from support_agent.events.schema import AgentEvent, AgentEventAdapter, Mode
from support_agent.tools.chaos import ChaosPlan, ChaosPlanError

router = APIRouter(prefix="/api", tags=["chat"])

SessionId = Annotated[str, Path(pattern=SESSION_ID_PATTERN)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SessionCreated(BaseModel):
    session_id: str


class ChatRequest(_Body):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    message: str = Field(min_length=1, max_length=2000)
    mode: Mode = "chat"


class TranscriptMessage(BaseModel):
    turn_id: str
    mode: Mode
    role: str
    content: str
    created_at: datetime


class VerifiedCustomer(BaseModel):
    name: str | None
    tier: str | None


class SessionView(BaseModel):
    session_id: str
    created_at: datetime
    turn_count: int
    customer: VerifiedCustomer | None
    messages: list[TranscriptMessage]


class TurnTrace(BaseModel):
    turn_id: str
    mode: Mode
    user_text: str
    final_text: str
    outcome: str
    started_at: datetime
    total_ms: int
    ttft_ms: int | None
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd_list_price: float
    events: list[dict[str, Any]]


def _runner(request: Request) -> TurnRunner:
    runner: TurnRunner = request.app.state.runner
    return runner


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(request: Request) -> SessionCreated:
    return SessionCreated(session_id=await _runner(request).store.create())


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: SessionId) -> SessionView:
    store = _runner(request).store
    try:
        state = await store.load(session_id, history_limit=1)
    except SessionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found or expired") from None
    auth = state.auth
    return SessionView(
        session_id=state.id,
        created_at=state.created_at,
        turn_count=state.turn_count,
        customer=VerifiedCustomer(
            name=auth.customer_name, tier=auth.tier.value if auth.tier else None
        )
        if auth.verified
        else None,
        messages=[TranscriptMessage(**m) for m in await store.transcript(session_id)],
    )


@router.get("/sessions/{session_id}/turns")
async def get_turns(request: Request, session_id: SessionId) -> list[TurnTrace]:
    store = _runner(request).store
    try:
        await store.load(session_id, history_limit=1)
    except SessionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found or expired") from None
    return [TurnTrace(**t) for t in await store.turns(session_id)]


def _sse(event: AgentEvent | None) -> str:
    if event is None:
        return ": keep-alive\n\n"
    data = AgentEventAdapter.dump_json(event).decode()
    return f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"


@router.post(
    "/chat/stream",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "Agent events (SSE)"},
        404: {"description": "Session not found or expired"},
        409: {"description": "A turn is already running in this session"},
    },
)
async def chat_stream(
    request: Request,
    body: ChatRequest,
    x_chaos: Annotated[
        str | None, Header(description="Fault plan, e.g. llm:rate_limit:once")
    ] = None,
) -> StreamingResponse:
    settings: Settings = request.app.state.settings
    runner = _runner(request)

    chaos: ChaosPlan | None = None
    if x_chaos:
        if not settings.chaos_enabled:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "chaos testing is disabled")
        try:
            chaos = ChaosPlan.parse(x_chaos, request.app.state.tools.names)
        except ChaosPlanError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"bad chaos plan: {exc}") from None

    try:
        events = await runner.start(body.session_id, body.message, mode=body.mode, chaos=chaos)
    except SessionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found or expired") from None
    except SessionBusy:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a reply is still being generated in this session"
        ) from None

    async def frames() -> AsyncIterator[str]:
        async for event in events:
            yield _sse(event)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx & friends)
            "Connection": "keep-alive",
        },
    )
