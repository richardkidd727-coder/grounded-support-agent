"""Runs tool calls requested by the model - and never lets one crash the conversation.

Every call returns a `ToolOutcome`: either the tool's output, or a structured error the model can
act on (fix its arguments, verify identity first, apologise and escalate). Retryable failures
(timeouts, 5xx, 429) are retried with exponential backoff; every tool is idempotent (refunds and
tickets use idempotency keys), so a retry can never double-apply an action.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from support_agent.logs import get_logger
from support_agent.tools.base import ErrorKind, Tool, ToolContext, ToolError

log = get_logger("tools")

MAX_RETRY_WAIT_S = 2.0


class ToolErrorInfo(BaseModel):
    kind: ErrorKind
    message: str  # written for the model: what went wrong and what to do next
    retryable: bool


class ToolOutcome(BaseModel):
    tool: str
    ok: bool
    output: dict[str, Any] | None = None
    error: ToolErrorInfo | None = None
    attempts: int
    duration_ms: int

    def for_model(self) -> dict[str, Any]:
        """The payload returned to the LLM as the function result."""
        if self.ok:
            return self.output or {}
        assert self.error is not None
        return {"error": self.error.kind.value, "message": self.error.message}


class ToolExecutor:
    def __init__(
        self,
        tools: Sequence[Tool[Any, Any]],
        *,
        max_attempts: int = 2,
        backoff_s: float = 0.25,
        timeout_override_s: float | None = None,
    ) -> None:
        self._tools = {t.name: t for t in tools}
        if len(self._tools) != len(tools):
            raise ValueError("duplicate tool names")
        self._max_attempts = max_attempts
        self._backoff_s = backoff_s
        self._timeout_override_s = timeout_override_s

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool[Any, Any] | None:
        return self._tools.get(name)

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    async def execute(
        self, name: str, raw_args: Mapping[str, Any] | None, ctx: ToolContext
    ) -> ToolOutcome:
        started = time.perf_counter()

        def finish(
            *, attempts: int, output: BaseModel | None = None, error: ToolError | None = None
        ) -> ToolOutcome:
            outcome = ToolOutcome(
                tool=name,
                ok=error is None,
                output=output.model_dump(mode="json") if output is not None else None,
                error=(
                    ToolErrorInfo(kind=error.kind, message=error.message, retryable=error.retryable)
                    if error
                    else None
                ),
                attempts=attempts,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            log.info(
                "tool",
                tool=name,
                ok=outcome.ok,
                error=error.kind.value if error else None,
                attempts=attempts,
                ms=outcome.duration_ms,
                session_id=ctx.session_id,
            )
            return outcome

        tool = self._tools.get(name)
        if tool is None:
            return finish(
                attempts=0,
                error=ToolError(
                    ErrorKind.UNKNOWN_TOOL, f"No tool named {name!r}. Available: {self.names}."
                ),
            )

        try:
            args = tool.input_model.model_validate(dict(raw_args or {}))
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
                for e in exc.errors(include_url=False)
            )
            return finish(
                attempts=0,
                error=ToolError(
                    ErrorKind.INVALID_ARGUMENTS, f"Invalid arguments - {problems}. Fix and retry."
                ),
            )

        if tool.requires_verification and not ctx.auth.verified:
            return finish(
                attempts=0,
                error=ToolError(
                    ErrorKind.NOT_VERIFIED,
                    "The customer's identity is not verified. Ask for their account email and "
                    "the last 4 digits of their card (or a recent invoice number), then call "
                    "verify_identity.",
                ),
            )

        timeout_s = self._timeout_override_s or tool.timeout_s
        last_error: ToolError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                async with asyncio.timeout(timeout_s):
                    if ctx.chaos is not None:
                        await ctx.chaos.inject(name, attempt, timeout_s)
                    output = await tool.run(args, ctx)
                return finish(attempts=attempt, output=output)
            except ToolError as exc:
                last_error = exc
            except TimeoutError:
                last_error = ToolError(
                    ErrorKind.TIMEOUT, f"{name} did not respond within {timeout_s:g}s."
                )
            except Exception:
                log.exception("tool_crashed", tool=name, session_id=ctx.session_id)
                last_error = ToolError(
                    ErrorKind.INTERNAL, f"{name} failed unexpectedly. Do not retry it."
                )

            if not last_error.retryable or attempt == self._max_attempts:
                break
            wait = last_error.retry_after_s or self._backoff_s * 2 ** (attempt - 1)
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT_S))

        assert last_error is not None
        if last_error.retryable:  # retries exhausted: tell the model how to degrade gracefully
            last_error = ToolError(
                last_error.kind,
                f"{last_error.message} Retried {self._max_attempts} times without success. "
                "Tell the customer this system is temporarily unavailable and offer to create "
                "an escalation ticket instead of guessing.",
            )
        return finish(attempts=attempt, error=last_error)
