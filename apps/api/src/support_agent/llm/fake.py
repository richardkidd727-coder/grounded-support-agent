"""A scripted model for tests and offline development.

Each call to `stream` consumes the next scripted reply (or calls a function of the request), so
tests can drive the agent loop through exact tool sequences and failure modes.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from support_agent.llm.types import (
    LlmError,
    LlmRequest,
    LlmResult,
    ProviderName,
    StreamItem,
    TextDelta,
    ToolCall,
    Usage,
)


@dataclass(frozen=True, slots=True)
class Reply:
    text: str = ""
    tool_calls: Sequence[tuple[str, dict[str, Any] | str]] = ()  # (name, args or raw JSON)
    error: LlmError | None = None
    error_after_text: bool = False  # stream `text`, then fail (a mid-stream failure)
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=100, output_tokens=20))


Script = Reply | Callable[[LlmRequest], Reply]


class FakeLlm:
    provider: ProviderName = "fake"

    def __init__(self, script: Sequence[Script], *, model: str = "fake-model") -> None:
        self.model = model
        self._script = list(script)
        self.requests: list[LlmRequest] = []

    @property
    def remaining(self) -> int:
        return len(self._script)

    async def stream(self, request: LlmRequest) -> AsyncIterator[StreamItem]:
        self.requests.append(request)
        if not self._script:
            raise AssertionError(f"{self.model}: no scripted reply left")
        step = self._script.pop(0)
        reply = step(request) if callable(step) else step
        if reply.error is not None and not reply.error_after_text:
            reply.error.provider = reply.error.provider or self.provider
            reply.error.model = reply.error.model or self.model
            raise reply.error
        for word in _chunks(reply.text):
            yield TextDelta(word)
        if reply.error is not None:
            reply.error.partial = bool(reply.text)
            raise reply.error
        calls = tuple(
            ToolCall(
                id=f"call_{self.model}_{len(self.requests)}_{i}",
                name=name,
                raw_arguments=args if isinstance(args, str) else json.dumps(args),
            )
            for i, (name, args) in enumerate(reply.tool_calls)
        )
        yield LlmResult(
            provider=self.provider,
            model=self.model,
            text=reply.text,
            tool_calls=calls,
            usage=reply.usage,
            finish_reason="tool_calls" if calls else "stop",
            latency_ms=1,
            ttft_ms=1 if reply.text else None,
        )


def _chunks(text: str) -> list[str]:
    """Word-sized deltas that concatenate back to `text` exactly."""
    return re.findall(r"\S+\s*|\s+", text)
