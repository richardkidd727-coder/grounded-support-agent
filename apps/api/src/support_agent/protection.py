"""Abuse protection: token-bucket rate limits and per-session budgets.

Every agent turn spends free-tier model quota (~8k tokens/minute per model, shared by all users),
so one noisy client can starve everyone. Limits apply per client IP (sessions created, turns)
and per session (turns, voice requests, total audio), and answer 429 with `Retry-After`, which
the UI turns into a countdown.

In-process state is deliberate: the API runs as one instance on the free tier. With several
replicas this would move to Redis (same interface).

Client IPs come from `request.client`, which uvicorn fills from `X-Forwarded-For` behind the
host's proxy (`--proxy-headers`). Trusting that header is only safe behind a proxy that sets
it; per-session limits hold regardless.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status

from support_agent.config import Settings


class TokenBucket:
    """`rate_per_minute` sustained, with bursts up to `burst`. Memory-bounded by `max_keys`."""

    def __init__(
        self,
        rate_per_minute: float,
        *,
        burst: float | None = None,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = rate_per_minute / 60.0
        self.capacity = burst if burst is not None else max(rate_per_minute, 1.0)
        self._max_keys = max_keys
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last update)

    def take(self, key: str, cost: float = 1.0) -> float | None:
        """Spend `cost` tokens. None if allowed, else seconds until it would be."""
        now = self._clock()
        tokens, last = self._buckets.pop(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.rate)
        if tokens >= cost:
            self._buckets[key] = (tokens - cost, now)  # re-insert: dict order = recency
            self._evict()
            return None
        self._buckets[key] = (tokens, now)
        return (cost - tokens) / self.rate if self.rate > 0 else math.inf

    def _evict(self) -> None:
        while len(self._buckets) > self._max_keys:
            self._buckets.pop(next(iter(self._buckets)))  # least recently used


@dataclass
class SessionBudget:
    """Lifetime caps per conversation (in-process; reset if the API restarts)."""

    max_turns: int
    max_audio_s: float
    turns: dict[str, int] = field(default_factory=dict)
    audio_s: dict[str, float] = field(default_factory=dict)


class Limits:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic) -> None:
        self.ip_sessions = TokenBucket(settings.ip_sessions_per_minute, clock=clock)
        self.ip_turns = TokenBucket(settings.ip_turns_per_minute, clock=clock)
        self.session_turns = TokenBucket(
            settings.rate_limit_per_minute,
            burst=max(3.0, settings.rate_limit_per_minute / 4),
            clock=clock,
        )
        self.session_voice = TokenBucket(settings.voice_requests_per_minute, clock=clock)
        self.budget = SessionBudget(
            max_turns=settings.session_max_turns, max_audio_s=settings.session_max_audio_s
        )


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _too_many(retry_after: float, what: str) -> HTTPException:
    seconds = max(1, math.ceil(retry_after))
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"Too many {what}. Please wait {seconds}s.",
        headers={"Retry-After": str(seconds)},
    )


def _limits(request: Request) -> Limits:
    limits: Limits = request.app.state.limits
    return limits


def check_new_session(request: Request) -> None:
    wait = _limits(request).ip_sessions.take(client_ip(request))
    if wait is not None:
        raise _too_many(wait, "new conversations")


def check_turn(request: Request, session_id: str) -> None:
    limits = _limits(request)
    budget = limits.budget
    if budget.turns.get(session_id, 0) >= budget.max_turns:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This conversation has reached its length limit - please start a new one.",
        )
    for bucket, key, what in (
        (limits.session_turns, session_id, "messages in this conversation"),
        (limits.ip_turns, client_ip(request), "messages from your network"),
    ):
        wait = bucket.take(key)
        if wait is not None:
            raise _too_many(wait, what)
    budget.turns[session_id] = budget.turns.get(session_id, 0) + 1
    _bound(budget.turns)


def check_voice(request: Request, session_id: str, audio_s: float = 0.0) -> None:
    limits = _limits(request)
    budget = limits.budget
    if budget.audio_s.get(session_id, 0.0) + audio_s > budget.max_audio_s:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Voice time for this conversation is used up - please continue by typing.",
        )
    wait = limits.session_voice.take(session_id)
    if wait is not None:
        raise _too_many(wait, "voice requests")
    budget.audio_s[session_id] = budget.audio_s.get(session_id, 0.0) + audio_s
    _bound(budget.audio_s)


def _bound(counters: dict[str, int] | dict[str, float], max_keys: int = 50_000) -> None:
    """Forget the oldest sessions' counters (they expire after SESSION_TTL_HOURS anyway)."""
    while len(counters) > max_keys:
        counters.pop(next(iter(counters)))
