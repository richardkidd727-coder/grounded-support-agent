"""Ordered model fallback with shared rate-limit awareness.

* A model call goes to the first model in the chain that is ready. "Not ready" means cooling down
  after a 429/quota error, or - when the provider reports it - not enough tokens left in the
  current per-minute window for this request (skipping ahead beats paying ~1s for a 429).
* Rate limits, quota errors, timeouts, 5xx and malformed tool calls fall through to the next
  model. The failure is yielded as an `AttemptFailed` item so the UI/trace can show it.
* Quotas are per API key, so cooldowns are process-wide: one session's 429 spares the others.
* If nothing is ready, wait for the soonest model when that is short, else fail fast with a
  `retry_after_s` the client can show.
* Never falls back once text has been streamed: the user has already seen it, and splicing a
  second model's answer onto the first would be worse than an honest error.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass

from support_agent.llm.types import (
    FALLBACK_KINDS,
    LlmClient,
    LlmError,
    LlmErrorKind,
    LlmRequest,
    LlmResult,
    TextDelta,
)
from support_agent.logs import get_logger

log = get_logger("llm")

DEFAULT_RATE_LIMIT_COOLDOWN_S = 10.0
AUTH_COOLDOWN_S = 600.0
_CHARS_PER_TOKEN = 3.5
_REQUEST_OVERHEAD_TOKENS = 200

FaultHook = Callable[[int], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AttemptFailed:
    error: LlmError


RouterItem = TextDelta | LlmResult | AttemptFailed


def estimate_request_tokens(request: LlmRequest) -> int:
    chars = sum(
        len(m.content) + sum(len(c.raw_arguments) + len(c.name) for c in m.tool_calls)
        for m in request.messages
    )
    chars += len(json.dumps(list(request.tools))) if request.tools else 0
    return int(chars / _CHARS_PER_TOKEN) + _REQUEST_OVERHEAD_TOKENS


class LlmRouter:
    def __init__(
        self,
        clients: Sequence[LlmClient],
        *,
        max_cooldown_wait_s: float = 12.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._clients = list(clients)
        self._cooldown_until = [0.0] * len(self._clients)
        self._max_wait = max_cooldown_wait_s
        self._clock = clock
        self._sleep = sleep

    @property
    def configured(self) -> bool:
        return bool(self._clients)

    @property
    def chain(self) -> list[str]:
        return [f"{c.provider}:{c.model}" for c in self._clients]

    def _wait_s(self, index: int, tokens: int) -> float:
        """Seconds until model `index` can probably take a request of `tokens` tokens."""
        cooldown = self._cooldown_until[index] - self._clock()
        budget_wait = getattr(self._clients[index], "budget_wait_s", None)
        budget = budget_wait(tokens) if callable(budget_wait) else 0.0
        return max(cooldown, budget, 0.0)

    def _cool(self, index: int, error: LlmError) -> None:
        if error.kind is LlmErrorKind.RATE_LIMITED:
            seconds = (
                error.retry_after_s
                if error.retry_after_s is not None
                else DEFAULT_RATE_LIMIT_COOLDOWN_S
            )
        elif error.kind in (LlmErrorKind.QUOTA, LlmErrorKind.AUTH):
            seconds = AUTH_COOLDOWN_S
        else:
            return
        self._cooldown_until[index] = max(self._cooldown_until[index], self._clock() + seconds)

    async def stream(
        self, request: LlmRequest, *, fault: FaultHook | None = None
    ) -> AsyncIterator[RouterItem]:
        if not self._clients:
            raise LlmError(LlmErrorKind.AUTH, "no LLM provider is configured (set an API key)")

        tokens = estimate_request_tokens(request)
        waits = {i: self._wait_s(i, tokens) for i in range(len(self._clients))}
        # Ready models in chain order, then the rest by how soon they free up.
        order = sorted(waits, key=lambda i: (waits[i] > 0, waits[i] if waits[i] > 0 else i))

        last_error: LlmError | None = None
        attempt = 0
        for position, index in enumerate(order):
            client = self._clients[index]
            wait = self._wait_s(index, tokens)
            if wait > self._max_wait:
                last_error = LlmError(
                    LlmErrorKind.RATE_LIMITED,
                    "rate limited; waiting for the per-minute budget to refill",
                    provider=client.provider,
                    model=client.model,
                    retry_after_s=round(wait, 1),
                )
                continue
            if wait > 0:
                await self._sleep(wait)

            attempt += 1
            try:
                if fault is not None:
                    await fault(attempt)
                async for item in client.stream(request):
                    yield item
                return
            except LlmError as exc:
                exc.provider = exc.provider or client.provider
                exc.model = exc.model or client.model
                log.warning(
                    "llm_attempt_failed",
                    provider=exc.provider,
                    model=exc.model,
                    kind=exc.kind.value,
                    partial=exc.partial,
                    retry_after_s=exc.retry_after_s,
                    message=exc.message[:160],
                )
                self._cool(index, exc)
                if exc.partial or exc.kind not in FALLBACK_KINDS:
                    raise
                last_error = exc
                if any(self._wait_s(i, tokens) <= self._max_wait for i in order[position + 1 :]):
                    yield AttemptFailed(exc)  # only announce a fallback that will happen

        assert last_error is not None
        if last_error.kind is LlmErrorKind.RATE_LIMITED:
            soonest = min(self._wait_s(i, tokens) for i in range(len(self._clients)))
            last_error.retry_after_s = max(round(soonest, 1), 1.0)
        raise last_error
