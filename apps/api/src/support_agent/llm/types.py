"""Provider-neutral chat types. The orchestrator only ever sees these."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]
ProviderName = Literal["gemini", "groq", "fake"]


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    raw_arguments: str  # exactly what the model produced (kept for history fidelity)

    @property
    def arguments(self) -> dict[str, Any] | None:
        """Parsed arguments, or None when the model emitted invalid JSON."""
        if not self.raw_arguments.strip():
            return {}
        try:
            value = json.loads(self.raw_arguments)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None  # role == "tool": which call this answers
    name: str | None = None  # role == "tool": the tool's name

    def to_openai(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.raw_arguments or "{}"},
                }
                for c in self.tool_calls
            ]
        if self.role == "tool":
            msg["tool_call_id"] = self.tool_call_id
            if self.name:
                msg["name"] = self.name
        return msg


@dataclass(frozen=True, slots=True)
class LlmRequest:
    messages: Sequence[ChatMessage]
    tools: Sequence[dict[str, Any]] = ()  # provider-neutral {"name","description","parameters"}
    temperature: float = 0.2
    max_output_tokens: int = 1024
    json_output: bool = False


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class LlmResult:
    """The final item of every successful stream."""

    provider: ProviderName
    model: str
    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    finish_reason: str | None
    latency_ms: int
    ttft_ms: int | None = None


class LlmErrorKind(StrEnum):
    RATE_LIMITED = "rate_limited"  # 429: back off / try another model
    QUOTA = "quota"  # free tier unavailable or daily quota spent
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"  # 5xx, connection errors
    BAD_OUTPUT = "bad_output"  # e.g. the provider rejected a malformed tool call
    AUTH = "auth"
    BAD_REQUEST = "bad_request"  # our fault - another model will not help


FALLBACK_KINDS = frozenset(
    {
        LlmErrorKind.RATE_LIMITED,
        LlmErrorKind.QUOTA,
        LlmErrorKind.TIMEOUT,
        LlmErrorKind.UNAVAILABLE,
        LlmErrorKind.BAD_OUTPUT,
        LlmErrorKind.AUTH,  # a revoked key on one provider should not take the agent down
    }
)


class LlmError(Exception):
    def __init__(
        self,
        kind: LlmErrorKind,
        message: str,
        *,
        provider: str = "",
        model: str = "",
        retry_after_s: float | None = None,
        partial: bool = False,
    ) -> None:
        super().__init__(f"{provider}:{model} {kind.value}: {message}")
        self.kind = kind
        self.message = message
        self.provider = provider
        self.model = model
        self.retry_after_s = retry_after_s
        self.partial = partial  # text was already streamed to the user when it failed
        self.connect_failed = False  # never reached the provider (safe to retry as-is)


StreamItem = TextDelta | LlmResult


class LlmClient(Protocol):
    provider: ProviderName
    model: str

    def stream(self, request: LlmRequest) -> AsyncIterator[StreamItem]:
        """Yield text deltas, then exactly one `LlmResult`. Raise `LlmError` on failure."""
        ...
