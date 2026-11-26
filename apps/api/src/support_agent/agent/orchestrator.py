"""The agent loop: one customer message in, one ordered stream of events out.

    user message
      -> [model call] --tool calls--> [executor: validate, check identity, run, retry]
            ^                              |  search_policies -> grounding gate (+ judge)
            |                              |  create_escalation_ticket -> `escalated`
            +------- tool results ---------+
      -> model call without tool calls = final answer (tokens streamed as they arrive)

Guarantees:
* Every turn ends with exactly one `turn.completed`, whatever fails. LLM outages, tool failures,
  the loop guard and unexpected bugs all degrade to an honest reply, never to a hung stream.
* Grounding is enforced in code: when the gate rejects a search, the excerpts are removed
  before the model sees them, so it cannot paraphrase weak matches into a confident answer.
* State changes (refunds, updates, tickets) are reported even if the model fails right after.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from support_agent.agent.context import StoredMessage, window
from support_agent.agent.grounding import GroundingJudge, GroundingVerdict
from support_agent.agent.presentation import completed_action, preview, redact_args, summarize
from support_agent.agent.prompts import interruption_note, system_prompt
from support_agent.config import Settings
from support_agent.events.schema import (
    AgentEvent,
    Citation,
    ErrorCode,
    ErrorEvent,
    Escalated,
    GuardrailResult,
    LlmCallUsage,
    Mode,
    StepCompleted,
    StepKind,
    StepStarted,
    TokenDelta,
    TurnCompleted,
    TurnMetrics,
    TurnStarted,
)
from support_agent.llm.pricing import list_price_usd
from support_agent.llm.router import AttemptFailed, LlmRouter
from support_agent.llm.types import (
    ChatMessage,
    LlmError,
    LlmErrorKind,
    LlmRequest,
    LlmResult,
    TextDelta,
    ToolCall,
)
from support_agent.logs import get_logger
from support_agent.tools.base import ErrorKind, SessionAuth, ToolContext, ToolDeps, utcnow
from support_agent.tools.chaos import ChaosPlan
from support_agent.tools.executor import ToolErrorInfo, ToolExecutor, ToolOutcome

log = get_logger("agent")

Sink = Callable[[AgentEvent], Awaitable[None]]
Outcome = Literal["answered", "escalated", "degraded"]

MAX_CITATIONS = 3

NOT_GROUNDED_GUIDANCE = (
    "No policy reliably answers this, so no excerpts are shown. Do not answer from general "
    "knowledge. A ticket was opened automatically (see `escalation`): tell the customer a "
    "specialist will follow up, giving that ticket id and the expected response time."
)
NOT_GROUNDED_NO_TICKET = (
    "No policy reliably answers this, so no excerpts are shown. Do not answer from general "
    "knowledge. Opening a ticket failed: apologise, and offer to try the escalation again."
)
UNVERIFIED_NOTE = (
    "Only general policies are visible until the customer is verified. Plan-specific terms "
    "(e.g. Pro or Enterprise refund windows and limits) need verification first."
)
_MODEL_HIDDEN_EXCERPT_KEYS = frozenset({"chunk_id", "doc_id"})  # ids invite fake citations

_LLM_ERROR_CODES = {
    LlmErrorKind.RATE_LIMITED: ErrorCode.QUOTA_EXHAUSTED,
    LlmErrorKind.QUOTA: ErrorCode.QUOTA_EXHAUSTED,
    LlmErrorKind.TIMEOUT: ErrorCode.LLM_TIMEOUT,
}


@dataclass(slots=True)
class TurnInput:
    session_id: str
    turn_id: str
    user_text: str
    mode: Mode
    history: list[StoredMessage]
    auth: SessionAuth  # mutated in place by verify_identity; the caller persists it
    chaos: ChaosPlan | None = None
    interrupted_after: str | None = None  # voice barge-in: what was heard of the last reply


@dataclass(slots=True)
class TurnOutput:
    messages: list[ChatMessage]  # this turn's messages, user first, in order
    final_text: str
    outcome: Outcome
    events: list[AgentEvent]
    llm_calls: list[LlmCallUsage]
    ttft_ms: int | None
    total_ms: int


class _Emitter:
    """Stamps session/turn/seq/ts onto events, records them, and forwards them to the sink."""

    def __init__(self, session_id: str, turn_id: str, sink: Sink, clock: Callable[[], datetime]):
        self._session_id = session_id
        self._turn_id = turn_id
        self._sink = sink
        self._clock = clock
        self.events: list[AgentEvent] = []

    async def __call__(self, event_type: Callable[..., AgentEvent], **fields: Any) -> None:
        event = event_type(
            session_id=self._session_id,
            turn_id=self._turn_id,
            seq=len(self.events),
            ts=self._clock(),
            **fields,
        )
        self.events.append(event)
        await self._sink(event)


@dataclass(slots=True)
class _Turn:
    """Mutable state of one running turn."""

    inp: TurnInput
    emit: _Emitter
    ctx: ToolContext
    started: float
    convo: list[ChatMessage]
    messages: list[ChatMessage]
    visible: list[str] = field(default_factory=list)
    llm_calls: list[LlmCallUsage] = field(default_factory=list)
    citations: dict[str, Citation] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
    escalated: bool = False
    ticket: dict[str, Any] | None = None  # first ticket opened this turn
    ttft_ms: int | None = None

    def elapsed_ms(self) -> int:
        return round((time.perf_counter() - self.started) * 1000)


def _step_id() -> str:
    return f"step_{uuid.uuid4().hex[:12]}"


class Agent:
    def __init__(
        self,
        *,
        llm: LlmRouter,
        judge: GroundingJudge,
        tools: ToolExecutor,
        deps: ToolDeps,
        settings: Settings,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._llm = llm
        self._judge = judge
        self._tools = tools
        self._deps = deps
        self._settings = settings
        self._clock = clock
        # Least privilege: account tools are only offered once the session is verified. The
        # executor enforces it anyway; hiding them also saves ~700 prompt tokens per call.
        self._specs_all = tools.specs()
        self._specs_unverified = [
            spec
            for spec in self._specs_all
            if not getattr(tools.get(spec["name"]), "requires_verification", False)
        ]

    async def run_turn(self, inp: TurnInput, sink: Sink) -> TurnOutput:
        emit = _Emitter(inp.session_id, inp.turn_id, sink, self._clock)
        user = ChatMessage("user", inp.user_text)
        note = (
            interruption_note(inp.interrupted_after) if inp.interrupted_after is not None else None
        )
        system = system_prompt(inp.auth, inp.mode, self._clock().date(), note)
        turn = _Turn(
            inp=inp,
            emit=emit,
            ctx=ToolContext(
                deps=self._deps,
                session_id=inp.session_id,
                auth=inp.auth,
                chaos=inp.chaos,
                clock=self._clock,
            ),
            started=time.perf_counter(),
            convo=[
                ChatMessage("system", system),
                *window(inp.history, self._settings.history_token_budget),
                user,
            ],
            messages=[user],
        )
        await emit(
            TurnStarted,
            mode=inp.mode,
            user_text=inp.user_text,
            interrupted_after=inp.interrupted_after,
        )
        try:
            outcome = await self._loop(turn)
        except Exception:  # last line of defence: a bug must not leave the stream hanging
            log.exception("turn_crashed", session_id=inp.session_id, turn_id=inp.turn_id)
            await emit(
                ErrorEvent,
                code=ErrorCode.INTERNAL,
                message="Something went wrong on our side.",
                retryable=True,
                retry_after_s=None,
            )
            await self._say(turn, self._degraded_text(turn, "something went wrong on my side"))
            outcome = "degraded"

        final_text = "".join(turn.visible).strip()
        total_ms = turn.elapsed_ms()
        await emit(TurnMetrics, ttft_ms=turn.ttft_ms, total_ms=total_ms, llm_calls=turn.llm_calls)
        await emit(
            TurnCompleted,
            final_text=final_text,
            citations=list(turn.citations.values())[:MAX_CITATIONS],
            outcome=outcome,
        )
        log.info(
            "turn",
            session_id=inp.session_id,
            turn_id=inp.turn_id,
            mode=inp.mode,
            outcome=outcome,
            ms=total_ms,
            ttft_ms=turn.ttft_ms,
            llm_calls=len(turn.llm_calls),
        )
        return TurnOutput(
            messages=turn.messages,
            final_text=final_text,
            outcome=outcome,
            events=emit.events,
            llm_calls=turn.llm_calls,
            ttft_ms=turn.ttft_ms,
            total_ms=total_ms,
        )

    # --- loop ------------------------------------------------------------------------------

    async def _loop(self, turn: _Turn) -> Outcome:
        for step in range(self._settings.agent_max_steps):
            try:
                result = await self._call_model(turn, step)
            except LlmError as exc:
                await turn.emit(
                    ErrorEvent,
                    code=_LLM_ERROR_CODES.get(exc.kind, ErrorCode.UPSTREAM_UNAVAILABLE),
                    message="The assistant is temporarily unavailable. Please try again shortly.",
                    retryable=True,
                    retry_after_s=exc.retry_after_s,
                )
                reason = (
                    "I've hit my usage limit for the moment"
                    if exc.kind in (LlmErrorKind.RATE_LIMITED, LlmErrorKind.QUOTA)
                    else "I'm having trouble reaching my language model"
                )
                await self._say(turn, self._degraded_text(turn, reason))
                return "degraded"

            assistant = ChatMessage("assistant", result.text, result.tool_calls)
            turn.convo.append(assistant)
            turn.messages.append(assistant)

            if not result.tool_calls:
                if not result.text.strip():
                    await self._say(
                        turn,
                        "Sorry, I didn't manage to put an answer together. Could you rephrase "
                        "that, or would you like me to pass this to a human agent?",
                    )
                    return "degraded"
                return "escalated" if turn.escalated else "answered"

            for call in result.tool_calls:
                tool_message = await self._run_tool(turn, call)
                turn.convo.append(tool_message)
                turn.messages.append(tool_message)

        log.warning(
            "loop_guard", session_id=turn.inp.session_id, steps=self._settings.agent_max_steps
        )
        await self._say(
            turn,
            self._degraded_text(turn, "this request needed more steps than I'm allowed to take"),
        )
        return "escalated" if turn.escalated else "degraded"

    async def _call_model(self, turn: _Turn, step: int) -> LlmResult:
        request = LlmRequest(
            messages=list(turn.convo),
            tools=self._specs_all if turn.ctx.auth.verified else self._specs_unverified,
            max_output_tokens=self._settings.agent_max_output_tokens,
        )
        chaos = turn.inp.chaos
        fault = chaos.llm_fault_for_step(step) if chaos is not None else None
        step_id = _step_id()
        step_started = time.perf_counter()
        label = "Thinking..." if step == 0 else "Reviewing results..."
        await turn.emit(
            StepStarted,
            step_id=step_id,
            kind=StepKind.LLM,
            name="llm",
            label=label,
            input_preview={"messages": len(request.messages), "step": step + 1},
        )
        needs_separator = bool("".join(turn.visible).strip())
        result: LlmResult | None = None
        try:
            async for item in self._llm.stream(request, fault=fault):
                if isinstance(item, TextDelta):
                    if needs_separator:
                        await self._token(turn, "\n\n")
                        needs_separator = False
                    await self._token(turn, item.text)
                elif isinstance(item, AttemptFailed):
                    err = item.error
                    await turn.emit(
                        StepCompleted,
                        step_id=step_id,
                        kind=StepKind.LLM,
                        name=f"{err.provider}:{err.model}",
                        status="error",
                        duration_ms=_since(step_started),
                        summary=f"{err.kind.value} - falling back to the next model",
                    )
                    step_id, step_started = _step_id(), time.perf_counter()
                    await turn.emit(
                        StepStarted,
                        step_id=step_id,
                        kind=StepKind.LLM,
                        name="llm",
                        label="Switching to a backup model...",
                        input_preview={"after": err.kind.value},
                    )
                else:
                    result = item
            if result is None:
                raise LlmError(LlmErrorKind.UNAVAILABLE, "stream ended without a result")
        except LlmError as exc:
            await turn.emit(
                StepCompleted,
                step_id=step_id,
                kind=StepKind.LLM,
                name=f"{exc.provider}:{exc.model}",
                status="error",
                duration_ms=_since(step_started),
                summary=exc.kind.value,
            )
            raise

        turn.llm_calls.append(self._usage(result, "agent"))
        tools = [c.name for c in result.tool_calls]
        await turn.emit(
            StepCompleted,
            step_id=step_id,
            kind=StepKind.LLM,
            name=f"{result.provider}:{result.model}",
            status="ok",
            duration_ms=_since(step_started),
            summary=(
                f"{result.usage.input_tokens} in / {result.usage.output_tokens} out tokens"
                + (f" -> {', '.join(tools)}" if tools else "")
            ),
            output_preview={
                "model": result.model,
                "finish_reason": result.finish_reason,
                "tool_calls": tools,
                "latency_ms": result.latency_ms,
            },
        )
        return result

    # --- tools ----------------------------------------------------------------------------

    async def _run_tool(self, turn: _Turn, call: ToolCall) -> ChatMessage:
        payload = await self._execute(turn, call.name, call.arguments)
        return ChatMessage(
            "tool",
            json.dumps(payload, separators=(",", ":"), default=str),
            tool_call_id=call.id,
            name=call.name,
        )

    async def _execute(
        self,
        turn: _Turn,
        name: str,
        args: dict[str, Any] | None,
        *,
        triggered_by: str | None = None,
    ) -> dict[str, Any]:
        """Run one tool as a visible step; returns the payload the model gets back."""
        tool = self._tools.get(name)
        step_id = _step_id()
        input_preview = redact_args(args)
        if triggered_by and input_preview is not None:
            input_preview["triggered_by"] = triggered_by
        await turn.emit(
            StepStarted,
            step_id=step_id,
            kind=StepKind.TOOL,
            name=name,
            label=tool.label if tool else f"Running {name}...",
            input_preview=input_preview,
        )
        if args is None:
            outcome = ToolOutcome(
                tool=name,
                ok=False,
                error=ToolErrorInfo(
                    kind=ErrorKind.INVALID_ARGUMENTS,
                    message="Arguments were not valid JSON. Retry with a JSON object.",
                    retryable=False,
                ),
                attempts=0,
                duration_ms=0,
            )
        else:
            outcome = await self._tools.execute(name, args, turn.ctx)

        payload = outcome.for_model()
        await turn.emit(
            StepCompleted,
            step_id=step_id,
            kind=StepKind.TOOL,
            name=name,
            status="ok" if outcome.ok else "error",
            duration_ms=outcome.duration_ms,
            summary=summarize(outcome),
            output_preview=preview(payload),
        )

        action = completed_action(outcome)
        if action:
            turn.actions.append(action)
        if outcome.ok and outcome.output is not None:
            if name == "search_policies":
                payload = await self._ground(
                    turn, outcome.output, str(args.get("query", "")) if args else ""
                )
            elif name == "create_escalation_ticket":
                turn.escalated = True
                turn.ticket = turn.ticket or {
                    "ticket_id": outcome.output["ticket_id"],
                    "expected_first_response": outcome.output["expected_first_response"],
                }
                await turn.emit(
                    Escalated,
                    ticket_id=outcome.output["ticket_id"],
                    reason=str((args or {}).get("summary", ""))[:300],
                    priority=outcome.output["priority"],
                )
        return payload

    async def _escalate_ungrounded(self, turn: _Turn, query: str, reasons: list[str]) -> None:
        """The low-confidence path, enforced in code rather than left to the model.

        A model told "please escalate" may instead apologise vaguely - or invent a ticket number
        (observed with a small fallback model). So the orchestrator opens the ticket itself, as a
        visible step, and the model only gets the real id to relay. One ticket per turn at most.
        """
        if turn.ticket is not None:
            return
        steps = [f"Searched the knowledge base for: {query}"[:200], *reasons]
        await self._execute(
            turn,
            "create_escalation_ticket",
            {
                "summary": f"Question not covered by our policies: {turn.inp.user_text}"[:1000],
                "category": "other",
                "priority": "normal",
                "attempted_steps": steps[:10],
            },
            triggered_by="guardrail",
        )

    async def _ground(self, turn: _Turn, result: dict[str, Any], query: str) -> dict[str, Any]:
        """Apply the confidence gate to a search result; returns what the model may see."""
        settings = self._settings
        band = result.get("confidence")
        top = float(result.get("top_similarity") or 0.0)
        excerpts: list[dict[str, Any]] = list(result.get("excerpts") or [])
        verdict: GroundingVerdict | None = None

        if band == "uncertain":
            step_id, started = _step_id(), time.perf_counter()
            await turn.emit(
                StepStarted,
                step_id=step_id,
                kind=StepKind.GUARDRAIL,
                name="grounding_judge",
                label="Double-checking the policy match...",
                input_preview={"similarity": top, "excerpts": len(excerpts)},
            )
            question = (
                turn.inp.user_text if not query else f"{turn.inp.user_text}\n(search: {query})"
            )
            verdict, judge_result = await self._judge.judge(question, excerpts)
            if judge_result is not None:
                turn.llm_calls.append(self._usage(judge_result, "judge"))
            await turn.emit(
                StepCompleted,
                step_id=step_id,
                kind=StepKind.GUARDRAIL,
                name="grounding_judge",
                status="ok" if verdict.available else "error",
                duration_ms=_since(started),
                summary=f"{_verdict_word(verdict)} ({verdict.score})",
                output_preview={
                    "reason": verdict.reason,
                    "model": judge_result.model if judge_result else None,
                },
            )

        grounded = band == "confident" or (verdict is not None and verdict.supported)
        if band == "confident":
            reasons = [f"similarity {top:.2f} >= {settings.retrieval_accept_above} (confident)"]
        elif band == "uncertain":
            assert verdict is not None
            reasons = [
                f"similarity {top:.2f} in the uncertain band "
                f"[{settings.retrieval_reject_below}, {settings.retrieval_accept_above})",
                f"judge: {verdict.reason or _verdict_word(verdict)}",
            ]
        else:
            reasons = [
                f"similarity {top:.2f} < {settings.retrieval_reject_below} (no relevant policy)"
            ]

        await turn.emit(
            GuardrailResult,
            retrieval_score=round(top, 3),
            grounding_score=verdict.score if verdict else None,
            policy_ok=grounded,
            confidence=round(min(max(verdict.score if verdict else top, 0.0), 1.0), 3),
            decision="answer" if grounded else "escalate",
            reasons=reasons,
        )

        if not grounded:
            await self._escalate_ungrounded(turn, query, reasons)
            return {
                "confidence": "insufficient",
                "excerpts": [],
                "escalation": turn.ticket,
                "guidance": NOT_GROUNDED_GUIDANCE if turn.ticket else NOT_GROUNDED_NO_TICKET,
            }
        for excerpt in sorted(excerpts, key=lambda e: float(e.get("similarity", 0)), reverse=True):
            chunk_id = str(excerpt.get("chunk_id"))
            section = str(excerpt.get("section", ""))
            turn.citations.setdefault(
                chunk_id,
                Citation(
                    chunk_id=chunk_id,
                    doc_id=str(excerpt.get("doc_id", "")),
                    title=section.split(" > ")[0],
                    section_path=section,
                    score=float(excerpt.get("similarity", 0)),
                ),
            )
        visible = {
            **result,
            "excerpts": [
                {k: v for k, v in e.items() if k not in _MODEL_HIDDEN_EXCERPT_KEYS}
                for e in excerpts
            ],
        }
        if result.get("audience") == "general":
            visible["note"] = UNVERIFIED_NOTE
        return visible

    # --- output helpers --------------------------------------------------------------------

    async def _token(self, turn: _Turn, text: str) -> None:
        if turn.ttft_ms is None and text.strip():
            turn.ttft_ms = turn.elapsed_ms()
        turn.visible.append(text)
        await turn.emit(TokenDelta, text=text)

    async def _say(self, turn: _Turn, text: str) -> None:
        """Agent-authored text (degraded paths): streamed like model output and remembered."""
        if "".join(turn.visible).strip():
            await self._token(turn, "\n\n")
        await self._token(turn, text)
        message = ChatMessage("assistant", text)
        turn.messages.append(message)
        turn.convo.append(message)

    @staticmethod
    def _degraded_text(turn: _Turn, reason: str) -> str:
        text = f"Sorry - {reason}, so I can't finish this reply right now."
        if turn.actions:
            text += " Before that, " + "; ".join(turn.actions) + "."
        text += " Please try again in a moment, or ask me to pass this to a human agent."
        return text

    def _usage(self, result: LlmResult, purpose: Literal["agent", "judge"]) -> LlmCallUsage:
        cost = list_price_usd(result.model, result.usage)
        return LlmCallUsage(
            provider=result.provider,
            model=result.model,
            purpose=purpose,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cached_input_tokens=result.usage.cached_input_tokens,
            latency_ms=result.latency_ms,
            cost_usd_list_price=cost,
            cost_usd_actual=cost if self._settings.llm_billing == "paid" else 0.0,
        )


def _verdict_word(verdict: GroundingVerdict) -> str:
    return "supported" if verdict.supported else "not supported"


def _since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
