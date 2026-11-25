"""Tool contract shared by every tool and the executor.

Design rules:
* Inputs and outputs are Pydantic models. The input model *is* the schema the LLM sees, so the
  model cannot pass a field that isn't declared (``extra="forbid"``).
* Anything security-relevant comes from `ToolContext`, never from model arguments: which customer
  is verified, their tier, the session id. A model cannot "claim" to be someone else.
* Expected business outcomes (refund denied, update not allowed) are *successful* results with a
  decision field. `ToolError` is only for failures: bad arguments, unmet preconditions, upstream
  problems.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from support_agent.domain import Tier

if TYPE_CHECKING:
    from support_agent.config import Settings
    from support_agent.db import Database
    from support_agent.rag.retriever import HybridRetriever
    from support_agent.tools.chaos import ChaosPlan


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorKind(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_VERIFIED = "identity_not_verified"
    VERIFICATION_LOCKED = "verification_locked"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UPSTREAM = "upstream_error"
    INTERNAL = "internal_error"


RETRYABLE_KINDS = frozenset({ErrorKind.TIMEOUT, ErrorKind.RATE_LIMITED, ErrorKind.UPSTREAM})


class ToolError(Exception):
    def __init__(self, kind: ErrorKind, message: str, *, retry_after_s: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after_s = retry_after_s

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS


@dataclass(slots=True)
class SessionAuth:
    """Per-conversation identity state. Tools read it; only `verify_identity` changes it."""

    customer_id: str | None = None
    customer_name: str | None = None
    tier: Tier | None = None
    failed_attempts: int = 0

    @property
    def verified(self) -> bool:
        return self.customer_id is not None


@dataclass(frozen=True, slots=True)
class ToolDeps:
    db: Database
    retriever: HybridRetriever
    settings: Settings


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ToolContext:
    deps: ToolDeps
    session_id: str
    auth: SessionAuth = field(default_factory=SessionAuth)
    chaos: ChaosPlan | None = None
    clock: Callable[[], datetime] = utcnow

    def require_customer(self) -> str:
        if self.auth.customer_id is None:  # the executor checks this first; belt and braces
            raise ToolError(ErrorKind.NOT_VERIFIED, "Verify the customer's identity first.")
        return self.auth.customer_id


class Tool[InT: ToolInput, OutT: ToolOutput](ABC):
    name: str
    description: str  # written for the model: when to use it, what it returns
    label: str  # UI status line while running, e.g. "Verifying identity..."
    input_model: type[InT]
    output_model: type[OutT]
    requires_verification: bool = False
    timeout_s: float = 8.0

    @abstractmethod
    async def run(self, args: InT, ctx: ToolContext) -> OutT: ...

    def spec(self) -> dict[str, Any]:
        """Provider-neutral function declaration (adapted per LLM provider in the orchestrator)."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_model.model_json_schema(),
        }
