"""The agent event protocol.

Every turn - chat or voice - is described by one ordered stream of these events. The chat SSE
endpoint, the voice transcript sync and the observability drawer all consume the same union.
This module is the single source of truth; the TypeScript types in `packages/shared-types`
are generated from it (`npm run gen:types`), and CI fails if they drift.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Mode = Literal["chat", "voice"]


class _Model(BaseModel):
    # Serialised events always carry every field (incl. defaulted ones such as `type`), so the
    # exported schema marks them required - keeps the TS discriminated union narrowable.
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_serialization_defaults_required=True
    )


class _Event(_Model):
    session_id: str
    turn_id: str
    seq: int = Field(ge=0, description="Monotonic per turn; lets clients detect gaps/reorders.")
    ts: datetime


# --- shared payloads ------------------------------------------------------------


class StepKind(StrEnum):
    LLM = "llm"
    TOOL = "tool"
    RETRIEVAL = "retrieval"
    GUARDRAIL = "guardrail"


class Citation(_Model):
    chunk_id: str
    doc_id: str
    title: str
    section_path: str
    score: float


class LlmCallUsage(_Model):
    provider: Literal["gemini", "groq", "fake"]
    model: str
    purpose: Literal["agent", "judge", "voice"]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(ge=0)
    cost_usd_list_price: float = Field(ge=0, description="Estimate at paid list prices.")
    cost_usd_actual: float = Field(ge=0, description="What we actually paid (0 on free tiers).")


class ErrorCode(StrEnum):
    RATE_LIMITED = "rate_limited"  # our own limiter
    QUOTA_EXHAUSTED = "quota_exhausted"  # upstream free-tier quota, all fallbacks spent
    LLM_TIMEOUT = "llm_timeout"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    TOOL_FAILED = "tool_failed"
    INVALID_REQUEST = "invalid_request"
    INTERNAL = "internal"


# --- events -----------------------------------------------------------------------


class TurnStarted(_Event):
    type: Literal["turn.started"] = "turn.started"
    mode: Mode
    user_text: str
    interrupted_after: str | None = Field(
        default=None,
        description="Voice barge-in: what the customer heard of the previous reply before "
        "talking over it (empty string: nothing). Null when the previous reply was not cut off.",
    )


class StepStarted(_Event):
    type: Literal["step.started"] = "step.started"
    step_id: str
    kind: StepKind
    name: str = Field(description="Machine name, e.g. the tool name.")
    label: str = Field(description='Human status line, e.g. "Verifying account tier..."')
    input_preview: dict[str, Any] | None = None


class StepCompleted(_Event):
    type: Literal["step.completed"] = "step.completed"
    step_id: str
    kind: StepKind
    name: str
    status: Literal["ok", "error", "skipped"]
    duration_ms: int = Field(ge=0)
    summary: str | None = None
    output_preview: dict[str, Any] | None = None


class TokenDelta(_Event):
    type: Literal["token"] = "token"
    text: str


class GuardrailResult(_Event):
    type: Literal["guardrail.result"] = "guardrail.result"
    retrieval_score: float | None = Field(default=None, description="Top hybrid score, if RAG ran.")
    grounding_score: float | None = Field(default=None, description="Judge score, if it ran.")
    policy_ok: bool
    confidence: float = Field(ge=0, le=1)
    decision: Literal["answer", "escalate"]
    reasons: list[str] = Field(default_factory=list)


class Escalated(_Event):
    type: Literal["escalated"] = "escalated"
    ticket_id: str
    reason: str
    priority: Literal["low", "normal", "high", "urgent"]


class TurnMetrics(_Event):
    type: Literal["metrics"] = "metrics"
    ttft_ms: int | None = Field(default=None, ge=0, description="Time to first token.")
    total_ms: int = Field(ge=0)
    llm_calls: list[LlmCallUsage] = Field(default_factory=list)


class TurnCompleted(_Event):
    type: Literal["turn.completed"] = "turn.completed"
    final_text: str
    citations: list[Citation] = Field(default_factory=list)
    outcome: Literal["answered", "escalated", "degraded"]


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    code: ErrorCode
    message: str = Field(description="Safe to show to the end user.")
    retryable: bool
    retry_after_s: float | None = Field(default=None, ge=0)


AgentEvent = Annotated[
    TurnStarted
    | StepStarted
    | StepCompleted
    | TokenDelta
    | GuardrailResult
    | Escalated
    | TurnMetrics
    | TurnCompleted
    | ErrorEvent,
    Field(discriminator="type"),
]

AgentEventAdapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)
