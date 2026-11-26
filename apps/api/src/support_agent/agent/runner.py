"""Runs turns in the background and hands their events to whoever is listening.

Why a background task instead of running the agent inside the HTTP response:
* If the browser disconnects mid-turn (tab closed, mobile network drop), a refund that is half way
  through must still finish and be recorded. The turn owns its own task; the HTTP stream is only
  a listener.
* One turn per session at a time: a second message while a turn is running gets 409 instead of
  interleaving two tool loops over the same identity state.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Final

from support_agent.agent.orchestrator import Agent, TurnInput
from support_agent.agent.store import SessionStore
from support_agent.events.schema import AgentEvent, Mode
from support_agent.logs import get_logger
from support_agent.tools.base import utcnow
from support_agent.tools.chaos import ChaosPlan

log = get_logger("runner")

_END: Final = object()


class SessionBusy(Exception):  # noqa: N818
    pass


class TurnRunner:
    def __init__(self, agent: Agent, store: SessionStore) -> None:
        self._agent = agent
        self._store = store
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def store(self) -> SessionStore:
        return self._store

    async def start(
        self,
        session_id: str,
        user_text: str,
        *,
        mode: Mode = "chat",
        chaos: ChaosPlan | None = None,
        interrupted_after: str | None = None,
        heartbeat_s: float = 15.0,
    ) -> AsyncIterator[AgentEvent | None]:
        """Start a turn. Raises `SessionNotFound` / `SessionBusy` before anything streams.

        The returned iterator yields events, and `None` as a keep-alive when nothing has
        happened for `heartbeat_s` (so proxies don't close an idle stream).
        """
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        if lock.locked():
            raise SessionBusy(session_id)
        await lock.acquire()  # uncontended: returns without yielding to the loop
        try:
            state = await self._store.load(session_id)
        except BaseException:
            self._release(session_id, lock)
            raise

        queue: asyncio.Queue[object] = asyncio.Queue()
        turn = TurnInput(
            session_id=session_id,
            turn_id=f"turn_{uuid.uuid4().hex[:16]}",
            user_text=user_text,
            mode=mode,
            history=state.history,
            auth=state.auth,
            chaos=chaos,
            interrupted_after=interrupted_after,
        )
        task = asyncio.create_task(self._run(turn, queue, lock), name=f"turn:{turn.turn_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self._listen(queue, heartbeat_s)

    async def _run(self, turn: TurnInput, queue: asyncio.Queue[object], lock: asyncio.Lock) -> None:
        started_at = utcnow()

        async def sink(event: AgentEvent) -> None:
            queue.put_nowait(event)

        try:
            output = await self._agent.run_turn(turn, sink)
            try:
                await self._store.save_turn(
                    turn.session_id,
                    turn_id=turn.turn_id,
                    mode=turn.mode,
                    user_text=turn.user_text,
                    auth=turn.auth,
                    output=output,
                    started_at=started_at,
                )
            except Exception:
                log.exception("turn_not_saved", session_id=turn.session_id, turn_id=turn.turn_id)
        finally:
            queue.put_nowait(_END)
            self._release(turn.session_id, lock)

    @staticmethod
    async def _listen(
        queue: asyncio.Queue[object], heartbeat_s: float
    ) -> AsyncIterator[AgentEvent | None]:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                yield None
                continue
            if item is _END:
                return
            yield item  # type: ignore[misc]

    def _release(self, session_id: str, lock: asyncio.Lock) -> None:
        lock.release()
        if self._locks.get(session_id) is lock:
            del self._locks[session_id]

    async def shutdown(self, grace_s: float = 10.0) -> None:
        """Let in-flight turns finish (they may be mid-refund), then cancel stragglers."""
        if not self._tasks:
            return
        _, pending = await asyncio.wait(set(self._tasks), timeout=grace_s)
        for task in pending:
            task.cancel()
        if pending:
            log.warning("turns_cancelled_on_shutdown", count=len(pending))
