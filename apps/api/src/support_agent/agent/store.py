"""Postgres-backed conversation sessions: identity state, message history, turn traces.

The session id is a bearer capability - whoever holds it continues the conversation as the
verified customer - so ids are 144-bit random tokens and sessions expire after
`SESSION_TTL_HOURS` of inactivity.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import text

from support_agent.agent.context import StoredMessage
from support_agent.agent.orchestrator import TurnOutput
from support_agent.db import Database
from support_agent.domain import Tier
from support_agent.events.schema import AgentEventAdapter, Mode
from support_agent.llm.types import ChatMessage, ToolCall
from support_agent.tools.base import SessionAuth, utcnow

SESSION_ID_PATTERN = r"^sess_[A-Za-z0-9_-]{16,64}$"
HISTORY_LIMIT = 80  # messages loaded per turn; the context window trims further


class SessionNotFound(Exception):  # noqa: N818 - reads better at call sites
    pass


@dataclass(slots=True)
class SessionState:
    id: str
    created_at: datetime
    updated_at: datetime
    auth: SessionAuth
    turn_count: int
    history: list[StoredMessage]


def new_session_id() -> str:
    return f"sess_{secrets.token_urlsafe(18)}"


def _auth_to_json(auth: SessionAuth) -> str:
    return json.dumps(
        {
            "customer_id": auth.customer_id,
            "customer_name": auth.customer_name,
            "tier": auth.tier.value if auth.tier else None,
            "failed_attempts": auth.failed_attempts,
        }
    )


def _auth_from_json(data: dict[str, Any]) -> SessionAuth:
    tier = data.get("tier")
    return SessionAuth(
        customer_id=data.get("customer_id"),
        customer_name=data.get("customer_name"),
        tier=Tier(tier) if tier else None,
        failed_attempts=int(data.get("failed_attempts") or 0),
    )


def _row_to_message(row: Any) -> StoredMessage:
    calls = tuple(
        ToolCall(id=c["id"], name=c["name"], raw_arguments=c["arguments"])
        for c in (row.tool_calls or [])
    )
    return StoredMessage(
        turn_id=row.turn_id,
        mode=cast(Mode, row.mode),
        message=ChatMessage(
            role=row.role,
            content=row.content,
            tool_calls=calls,
            tool_call_id=row.tool_call_id,
            name=row.tool_name,
        ),
    )


class SessionStore:
    def __init__(
        self,
        db: Database,
        *,
        ttl_hours: int = 24,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._db = db
        self._ttl = timedelta(hours=ttl_hours)
        self._clock = clock

    async def create(self) -> str:
        session_id = new_session_id()
        now = self._clock()
        async with self._db.engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO chat_sessions (id, created_at, updated_at) "
                    "VALUES (:id, :now, :now)"
                ),
                {"id": session_id, "now": now},
            )
        return session_id

    async def load(self, session_id: str, *, history_limit: int = HISTORY_LIMIT) -> SessionState:
        async with self._db.engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT id, created_at, updated_at, auth, turn_count "
                        "FROM chat_sessions WHERE id = :id"
                    ),
                    {"id": session_id},
                )
            ).one_or_none()
            if row is None or row.updated_at < self._clock() - self._ttl:
                raise SessionNotFound(session_id)
            rows = (
                await conn.execute(
                    text(
                        "SELECT * FROM (SELECT id, turn_id, mode, role, content, tool_calls, "
                        "tool_call_id, tool_name FROM chat_messages WHERE session_id = :id "
                        "ORDER BY id DESC LIMIT :limit) recent ORDER BY id"
                    ),
                    {"id": session_id, "limit": history_limit},
                )
            ).all()
        history = [_row_to_message(r) for r in rows]
        # The LIMIT may cut a turn in half; drop the partial oldest turn.
        if len(rows) == history_limit and history:
            first_turn = history[0].turn_id
            history = [m for m in history if m.turn_id != first_turn] or history
        return SessionState(
            id=row.id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            auth=_auth_from_json(row.auth or {}),
            turn_count=row.turn_count,
            history=history,
        )

    async def save_turn(
        self,
        session_id: str,
        *,
        turn_id: str,
        mode: Mode,
        user_text: str,
        auth: SessionAuth,
        output: TurnOutput,
        started_at: datetime,
    ) -> None:
        """Persist a finished turn atomically: its messages, the session's identity, the trace."""
        message_rows = [
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "mode": mode,
                "role": m.role,
                "content": m.content,
                "tool_calls": (
                    json.dumps(
                        [
                            {"id": c.id, "name": c.name, "arguments": c.raw_arguments}
                            for c in m.tool_calls
                        ]
                    )
                    if m.tool_calls
                    else None
                ),
                "tool_call_id": m.tool_call_id,
                "tool_name": m.name,
            }
            for m in output.messages
            if m.role != "system"
        ]
        events = [AgentEventAdapter.dump_python(e, mode="json") for e in output.events]
        async with self._db.engine.begin() as conn:
            if message_rows:
                await conn.execute(
                    text(
                        "INSERT INTO chat_messages (session_id, turn_id, mode, role, content, "
                        "tool_calls, tool_call_id, tool_name) VALUES (:session_id, :turn_id, "
                        ":mode, :role, :content, CAST(:tool_calls AS jsonb), :tool_call_id, "
                        ":tool_name)"
                    ),
                    message_rows,
                )
            await conn.execute(
                text(
                    "UPDATE chat_sessions SET auth = CAST(:auth AS jsonb), updated_at = :now, "
                    "turn_count = turn_count + 1 WHERE id = :id"
                ),
                {"id": session_id, "auth": _auth_to_json(auth), "now": self._clock()},
            )
            await conn.execute(
                text(
                    "INSERT INTO agent_turns (id, session_id, mode, user_text, final_text, "
                    "outcome, started_at, total_ms, ttft_ms, llm_calls, input_tokens, "
                    "output_tokens, cost_usd_list_price, events) "
                    "VALUES (:id, :session_id, :mode, :user_text, :final_text,"
                    " :outcome, :started_at, :total_ms, :ttft_ms, :llm_calls, "
                    ":input_tokens, :output_tokens, :cost, CAST(:events AS jsonb))"
                ),
                {
                    "id": turn_id,
                    "session_id": session_id,
                    "mode": mode,
                    "user_text": user_text,
                    "final_text": output.final_text,
                    "outcome": output.outcome,
                    "started_at": started_at,
                    "total_ms": output.total_ms,
                    "ttft_ms": output.ttft_ms,
                    "llm_calls": len(output.llm_calls),
                    "input_tokens": sum(c.input_tokens for c in output.llm_calls),
                    "output_tokens": sum(c.output_tokens for c in output.llm_calls),
                    "cost": sum(c.cost_usd_list_price for c in output.llm_calls),
                    "events": json.dumps(events),
                },
            )

    async def transcript(self, session_id: str) -> list[dict[str, Any]]:
        """What the customer saw: user messages and assistant text, oldest first."""
        async with self._db.engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT turn_id, mode, role, content, created_at FROM chat_messages "
                        "WHERE session_id = :id AND (role = 'user' OR (role = 'assistant' AND "
                        "content <> '')) ORDER BY id"
                    ),
                    {"id": session_id},
                )
            ).all()
        return [r._asdict() for r in rows]

    async def turns(self, session_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Recorded turn traces, newest first."""
        async with self._db.engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id AS turn_id, mode, user_text, final_text, outcome, started_at, "
                        "total_ms, ttft_ms, llm_calls, input_tokens, output_tokens, "
                        "cost_usd_list_price, events FROM agent_turns WHERE session_id = :id "
                        "ORDER BY started_at DESC LIMIT :limit"
                    ),
                    {"id": session_id, "limit": limit},
                )
            ).all()
        return [r._asdict() for r in rows]
