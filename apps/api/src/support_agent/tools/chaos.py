"""Fault injection for demos and tests.

Enabled only when `CHAOS_ENABLED=true`. A request opts in with a plan such as
``issue_refund:timeout:once, search_policies:error`` (the UI's chaos toggle sends it as a header):

* fault: ``timeout`` (the call hangs past its deadline), ``error`` (upstream 5xx) or
  ``rate_limit`` (429 with Retry-After)
* persistence: ``once`` (first attempt fails, the retry succeeds - shows recovery) or
  ``always`` (every attempt fails - shows graceful degradation). Default ``always``.
* ``*`` targets every tool.
* ``llm`` targets the agent's model calls (``llm:rate_limit:once`` shows provider fallback). It is
  never matched by ``*``. ``once`` fails the first model attempt of the turn's first model call;
  ``always`` fails every attempt (shows graceful degradation).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from support_agent.llm.types import LlmError, LlmErrorKind
from support_agent.tools.base import ErrorKind, ToolError

LLM_TARGET = "llm"

Fault = Literal["timeout", "error", "rate_limit"]
Persistence = Literal["once", "always"]
_FAULTS: frozenset[str] = frozenset({"timeout", "error", "rate_limit"})
_PERSISTENCE: frozenset[str] = frozenset({"once", "always"})


class ChaosPlanError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ChaosRule:
    fault: Fault
    persistence: Persistence = "always"


@dataclass(frozen=True, slots=True)
class ChaosPlan:
    rules: dict[str, ChaosRule]

    def rule_for(self, tool: str, attempt: int) -> ChaosRule | None:
        rule = self.rules.get(tool) or self.rules.get("*")
        if rule is None or (rule.persistence == "once" and attempt > 1):
            return None
        return rule

    async def inject(self, tool: str, attempt: int, timeout_s: float) -> None:
        """Raise (or hang) according to the plan. Called by the executor before the tool runs."""
        rule = self.rule_for(tool, attempt)
        if rule is None:
            return
        if rule.fault == "timeout":
            await asyncio.sleep(timeout_s + 1)  # the executor's deadline fires first
        elif rule.fault == "rate_limit":
            raise ToolError(
                ErrorKind.RATE_LIMITED, "[chaos] upstream rate limit", retry_after_s=0.5
            )
        else:
            raise ToolError(ErrorKind.UPSTREAM, "[chaos] upstream service returned 503")

    async def inject_llm(self, attempt: int) -> None:
        """Fault hook for `LlmRouter.stream`: fail a model attempt without calling the provider."""
        rule = self.rules.get(LLM_TARGET)
        if rule is None or (rule.persistence == "once" and attempt > 1):
            return
        kind = {
            "timeout": LlmErrorKind.TIMEOUT,
            "rate_limit": LlmErrorKind.RATE_LIMITED,
            "error": LlmErrorKind.UNAVAILABLE,
        }[rule.fault]
        # retry_after 0: an injected 429 must not put a real model on cooldown for other users
        raise LlmError(kind, f"[chaos] injected {rule.fault}", retry_after_s=0.0)

    def llm_fault_for_step(self, step: int) -> Callable[[int], Awaitable[None]] | None:
        """Fault hook for the turn's `step`-th model call, or None if it should run clean."""
        rule = self.rules.get(LLM_TARGET)
        if rule is None or (rule.persistence == "once" and step > 0):
            return None
        return self.inject_llm

    @classmethod
    def parse(cls, spec: str, known_tools: Iterable[str]) -> ChaosPlan:
        known = {*known_tools, "*", LLM_TARGET}
        rules: dict[str, ChaosRule] = {}
        for raw in spec.split(","):
            part = raw.strip()
            if not part:
                continue
            bits = [b.strip() for b in part.split(":")]
            if len(bits) not in (2, 3):
                raise ChaosPlanError(f"expected tool:fault[:once|always], got {part!r}")
            tool, fault = bits[0], bits[1]
            persistence = bits[2] if len(bits) == 3 else "always"
            if tool not in known:
                raise ChaosPlanError(f"unknown tool {tool!r}")
            if fault not in _FAULTS:
                raise ChaosPlanError(f"unknown fault {fault!r} (use {sorted(_FAULTS)})")
            if persistence not in _PERSISTENCE:
                raise ChaosPlanError(f"unknown persistence {persistence!r}")
            rules[tool] = ChaosRule(fault, persistence)  # type: ignore[arg-type]
        return cls(rules)
