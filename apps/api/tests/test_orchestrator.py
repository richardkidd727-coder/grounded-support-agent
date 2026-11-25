"""The agent loop, driven by scripted models and in-memory tools (no database, no network)."""

import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from support_agent.agent.context import StoredMessage
from support_agent.agent.grounding import GroundingJudge
from support_agent.agent.orchestrator import Agent, TurnInput, TurnOutput
from support_agent.config import Settings
from support_agent.domain import Tier
from support_agent.events.schema import (
    AgentEvent,
    ErrorCode,
    ErrorEvent,
    Escalated,
    GuardrailResult,
    StepCompleted,
    StepKind,
    StepStarted,
    TokenDelta,
    TurnCompleted,
    TurnMetrics,
    TurnStarted,
)
from support_agent.llm.fake import FakeLlm, Reply
from support_agent.llm.router import LlmRouter
from support_agent.llm.types import ChatMessage, LlmError, LlmErrorKind, LlmRequest
from support_agent.tools.base import SessionAuth, Tool, ToolContext, ToolDeps
from support_agent.tools.chaos import ChaosPlan
from support_agent.tools.executor import ToolExecutor
from support_agent.tools.identity import VerifyIdentityInput, VerifyIdentityOutput
from support_agent.tools.policies import PolicyExcerpt, SearchPoliciesInput, SearchPoliciesOutput
from support_agent.tools.refunds import IssueRefundInput, IssueRefundOutput
from support_agent.tools.tickets import CreateEscalationTicketInput, CreateEscalationTicketOutput

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)

# --- in-memory tools with the real schemas ---


class FakeVerify(Tool[VerifyIdentityInput, VerifyIdentityOutput]):
    name, label, description = "verify_identity", "Verifying identity...", "verify"
    input_model, output_model = VerifyIdentityInput, VerifyIdentityOutput

    async def run(self, args: VerifyIdentityInput, ctx: ToolContext) -> VerifyIdentityOutput:
        ctx.auth.customer_id, ctx.auth.customer_name, ctx.auth.tier = "cus_a", "Alice", Tier.PRO
        return VerifyIdentityOutput(
            verified=True, customer_name="Alice", attempts_remaining=3, message="ok"
        )


class FakeSearch(Tool[SearchPoliciesInput, SearchPoliciesOutput]):
    name, label, description = "search_policies", "Searching knowledge base...", "search"
    input_model, output_model = SearchPoliciesInput, SearchPoliciesOutput

    def __init__(self, band: str = "confident", similarity: float = 0.82) -> None:
        self.band, self.similarity = band, similarity

    async def run(self, args: SearchPoliciesInput, ctx: ToolContext) -> SearchPoliciesOutput:
        excerpts = [
            PolicyExcerpt(
                chunk_id="chunk-1",
                doc_id="refund-policy",
                section="Refund Policy > Pro plan",
                content="Pro charges are refundable within 30 days.",
                similarity=self.similarity,
            )
        ]
        return SearchPoliciesOutput(
            audience=ctx.auth.tier.value if ctx.auth.tier else "general",
            confidence=self.band,  # type: ignore[arg-type]
            top_similarity=self.similarity,
            excerpts=[] if self.band == "reject" else excerpts,
            guidance="g",
        )


class FakeRefund(Tool[IssueRefundInput, IssueRefundOutput]):
    name, label, description = "issue_refund", "Processing refund...", "refund"
    input_model, output_model = IssueRefundInput, IssueRefundOutput
    requires_verification = True

    async def run(self, args: IssueRefundInput, ctx: ToolContext) -> IssueRefundOutput:
        return IssueRefundOutput(
            decision="approved",
            explanation="within policy",
            refund_id="re_1",
            amount_refunded=args.amount,
            max_refundable=45.0,
            agent_limit=100.0,
        )


class FakeTicket(Tool[CreateEscalationTicketInput, CreateEscalationTicketOutput]):
    name, label, description = "create_escalation_ticket", "Escalating...", "ticket"
    input_model, output_model = CreateEscalationTicketInput, CreateEscalationTicketOutput

    def __init__(self) -> None:
        self.calls: list[CreateEscalationTicketInput] = []

    async def run(
        self, args: CreateEscalationTicketInput, ctx: ToolContext
    ) -> CreateEscalationTicketOutput:
        self.calls.append(args)
        return CreateEscalationTicketOutput(
            ticket_id=f"TCK-00000{len(self.calls)}",
            category=args.category,
            priority=args.priority,
            status="open",
            expected_first_response="within 8 business hours",
        )


# --- harness ---

VERIFY_ARGS = {"email": "alice@northpeak.example", "card_last4": "4242"}
REFUND_ARGS = {"invoice_id": "INV-2026-10231", "amount": 45, "reason": "goodwill", "note": "unused"}


class Harness:
    def __init__(
        self,
        script: list[Any],
        *,
        judge: list[Any] | None = None,
        search: FakeSearch | None = None,
        max_steps: int = 8,
        backup: list[Any] | None = None,
    ) -> None:
        self.llm = FakeLlm(script, model="primary")
        clients = [self.llm] + ([FakeLlm(backup, model="backup")] if backup is not None else [])
        self.judge_llm = FakeLlm(judge or [], model="judge")
        self.ticket = FakeTicket()
        self.settings = Settings(_env_file=None, agent_max_steps=max_steps)  # type: ignore[call-arg]
        tools = ToolExecutor(
            [FakeVerify(), search or FakeSearch(), FakeRefund(), self.ticket], backoff_s=0
        )
        self.agent = Agent(
            llm=LlmRouter(clients),
            judge=GroundingJudge(LlmRouter([self.judge_llm])),
            tools=tools,
            deps=ToolDeps(cast(Any, None), cast(Any, None), self.settings),
            settings=self.settings,
            clock=lambda: NOW,
        )
        self.events: list[AgentEvent] = []
        self.auth = SessionAuth()

    async def run(
        self,
        text: str = "hi",
        *,
        history: list[StoredMessage] | None = None,
        chaos: ChaosPlan | None = None,
    ) -> TurnOutput:
        async def sink(event: AgentEvent) -> None:
            self.events.append(event)

        return await self.agent.run_turn(
            TurnInput("sess", "turn_1", text, "chat", history or [], self.auth, chaos), sink
        )

    def of(self, kind: type) -> list[Any]:
        return [e for e in self.events if isinstance(e, kind)]

    def request(self, n: int) -> LlmRequest:
        return self.llm.requests[n]


def tool_payload(request: LlmRequest, name: str) -> dict[str, Any]:
    msg = next(m for m in reversed(request.messages) if m.role == "tool" and m.name == name)
    return cast(dict[str, Any], json.loads(msg.content))


# --- tests ---


async def test_plain_answer_streams_tokens_and_closes_the_turn() -> None:
    h = Harness([Reply("Hello there, how can I help?")])
    out = await h.run()
    types = [e.type for e in h.events]
    assert types[0] == "turn.started" and types[-2:] == ["metrics", "turn.completed"]
    assert [e.seq for e in h.events] == list(range(len(h.events)))  # gapless, ordered
    assert "".join(e.text for e in h.of(TokenDelta)) == "Hello there, how can I help?"
    assert out.final_text == "Hello there, how can I help?" and out.outcome == "answered"
    assert [m.role for m in out.messages] == ["user", "assistant"]
    metrics = h.of(TurnMetrics)[0]
    assert metrics.ttft_ms is not None and len(metrics.llm_calls) == 1
    assert metrics.llm_calls[0].cost_usd_actual == 0.0  # free tier


async def test_tool_loop_verify_then_refund_then_answer() -> None:
    h = Harness(
        [
            Reply(tool_calls=[("verify_identity", VERIFY_ARGS)]),
            Reply(tool_calls=[("issue_refund", REFUND_ARGS)]),
            Reply("Done - $45.00 refunded (re_1)."),
        ]
    )
    out = await h.run("refund please")
    tool_steps = [e for e in h.of(StepStarted) if e.kind is StepKind.TOOL]
    assert [s.name for s in tool_steps] == ["verify_identity", "issue_refund"]
    assert tool_steps[0].input_preview["card_last4"] == "****"  # redacted in events
    done = {e.name: e for e in h.of(StepCompleted) if e.kind is StepKind.TOOL}
    assert done["issue_refund"].summary == "Approved $45.00 (re_1)"
    assert h.auth.customer_id == "cus_a"  # identity persists via the session's auth
    assert [m.role for m in out.messages] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant"
    ]  # fmt: skip
    # the model saw each result, linked to its call id
    assert tool_payload(h.request(2), "issue_refund")["decision"] == "approved"


async def test_account_tools_are_only_offered_after_verification() -> None:
    h = Harness([Reply(tool_calls=[("verify_identity", VERIFY_ARGS)]), Reply("ok")])
    await h.run()
    before = {t["name"] for t in h.request(0).tools}
    after = {t["name"] for t in h.request(1).tools}
    assert "issue_refund" not in before and "verify_identity" in before
    assert "issue_refund" in after


async def test_confident_search_is_answered_with_citations_and_hidden_ids() -> None:
    h = Harness(
        [Reply(tool_calls=[("search_policies", {"query": "pro refunds"})]), Reply("30 days.")]
    )
    out = await h.run("refund window?")
    guard = h.of(GuardrailResult)[0]
    assert (guard.decision, guard.policy_ok, guard.grounding_score) == ("answer", True, None)
    assert h.judge_llm.requests == []  # confident band never pays for a judge call
    completed = h.of(TurnCompleted)[0]
    assert [c.section_path for c in completed.citations] == ["Refund Policy > Pro plan"]
    assert completed.citations[0].doc_id == "refund-policy"
    payload = tool_payload(h.request(1), "search_policies")
    assert "chunk_id" not in payload["excerpts"][0] and "doc_id" not in payload["excerpts"][0]
    assert "verified" in payload["note"]  # unverified: plan-specific terms need verification
    assert out.outcome == "answered"


async def test_rejected_search_escalates_in_code_and_withholds_excerpts() -> None:
    h = Harness(
        [
            Reply(tool_calls=[("search_policies", {"query": "nonprofit discounts"})]),
            Reply("A specialist will follow up: TCK-000001."),
        ],
        search=FakeSearch("reject", 0.41),
    )
    out = await h.run("Do you offer nonprofit discounts?")
    assert h.of(GuardrailResult)[0].decision == "escalate"
    assert len(h.ticket.calls) == 1
    assert h.ticket.calls[0].category == "other"
    auto = next(e for e in h.of(StepStarted) if e.name == "create_escalation_ticket")
    assert auto.input_preview["triggered_by"] == "guardrail"
    assert h.of(Escalated)[0].ticket_id == "TCK-000001"
    payload = tool_payload(h.request(1), "search_policies")
    assert payload["excerpts"] == [] and payload["escalation"]["ticket_id"] == "TCK-000001"
    assert out.outcome == "escalated"
    assert h.of(TurnCompleted)[0].citations == []


async def test_repeated_ungrounded_searches_open_only_one_ticket() -> None:
    h = Harness(
        [
            Reply(
                tool_calls=[
                    ("search_policies", {"query": "nonprofit discounts"}),
                    ("search_policies", {"query": "charity pricing"}),
                ]
            ),
            Reply("Ticket TCK-000001."),
        ],
        search=FakeSearch("reject", 0.4),
    )
    await h.run()
    assert len(h.ticket.calls) == 1


@pytest.mark.parametrize(
    ("judge_reply", "decision", "tickets"),
    [
        ('{"supported": true, "score": 0.86, "reason": "states the window"}', "answer", 0),
        ('{"supported": false, "score": 0.2, "reason": "different topic"}', "escalate", 1),
        ("not json at all", "escalate", 1),  # judge failure fails closed
    ],
)
async def test_uncertain_band_asks_the_judge(judge_reply: str, decision: str, tickets: int) -> None:
    h = Harness(
        [Reply(tool_calls=[("search_policies", {"query": "grey zone"})]), Reply("answer")],
        judge=[Reply(judge_reply)],
        search=FakeSearch("uncertain", 0.64),
    )
    out = await h.run("a grey-zone question")
    guard = h.of(GuardrailResult)[0]
    assert guard.decision == decision
    assert len(h.ticket.calls) == tickets
    judge_steps = [e for e in h.of(StepStarted) if e.kind is StepKind.GUARDRAIL]
    assert [s.name for s in judge_steps] == ["grounding_judge"]
    purposes = [c.purpose for c in h.of(TurnMetrics)[0].llm_calls]
    assert purposes == ["agent", "judge", "agent"]
    assert out.outcome == ("answered" if tickets == 0 else "escalated")


async def test_llm_outage_degrades_and_reports_what_already_happened() -> None:
    h = Harness(
        [
            Reply(tool_calls=[("verify_identity", VERIFY_ARGS)]),
            Reply(tool_calls=[("issue_refund", REFUND_ARGS)]),
            Reply(error=LlmError(LlmErrorKind.RATE_LIMITED, "quota", retry_after_s=30)),
        ]
    )
    out = await h.run()
    error = h.of(ErrorEvent)[0]
    assert (error.code, error.retryable, error.retry_after_s) == (
        ErrorCode.QUOTA_EXHAUSTED,
        True,
        30,
    )
    assert out.outcome == "degraded"
    assert "refund of $45.00 was approved (re_1)" in out.final_text
    assert out.messages[-1] == ChatMessage("assistant", out.messages[-1].content)  # remembered
    assert h.of(TurnCompleted)[0].final_text == out.final_text


async def test_fallback_is_visible_as_a_failed_then_retried_llm_step() -> None:
    h = Harness(
        [Reply("unused")],
        backup=[Reply("from the backup")],
    )
    out = await h.run(chaos=ChaosPlan.parse("llm:rate_limit:once", []))
    llm_done = [e for e in h.of(StepCompleted) if e.kind is StepKind.LLM]
    assert [(e.name, e.status) for e in llm_done] == [
        ("fake:primary", "error"),
        ("fake:backup", "ok"),
    ]
    assert out.final_text == "from the backup"
    assert h.llm.remaining == 1  # the primary was never called (fault injected before it)


async def test_invalid_json_arguments_are_returned_to_the_model() -> None:
    h = Harness([Reply(tool_calls=[("verify_identity", "{not json")]), Reply("Sorry, again?")])
    await h.run()
    payload = tool_payload(h.request(1), "verify_identity")
    assert payload["error"] == "invalid_arguments"


async def test_unverified_refund_attempt_is_refused_by_the_executor() -> None:
    h = Harness([Reply(tool_calls=[("issue_refund", REFUND_ARGS)]), Reply("Please verify first.")])
    await h.run()
    assert tool_payload(h.request(1), "issue_refund")["error"] == "identity_not_verified"


async def test_loop_guard_stops_a_model_that_never_answers() -> None:
    h = Harness(
        [Reply(tool_calls=[("search_policies", {"query": "again"})])] * 3,
        max_steps=3,
    )
    out = await h.run()
    assert out.outcome == "degraded"
    assert "more steps" in out.final_text
    assert len(h.llm.requests) == 3


async def test_empty_model_reply_is_not_left_blank() -> None:
    h = Harness([Reply("")])
    out = await h.run()
    assert out.outcome == "degraded" and out.final_text


async def test_text_before_and_after_tools_is_separated() -> None:
    h = Harness(
        [Reply("Let me check.", tool_calls=[("verify_identity", VERIFY_ARGS)]), Reply("Verified!")]
    )
    out = await h.run()
    assert out.final_text == "Let me check.\n\nVerified!"


async def test_history_is_sent_before_the_new_message() -> None:
    history = [
        StoredMessage("t0", "voice", ChatMessage("user", "earlier, by voice")),
        StoredMessage("t0", "voice", ChatMessage("assistant", "earlier answer")),
    ]
    h = Harness([Reply("ok")])
    await h.run("now typed", history=history)
    roles = [(m.role, m.content) for m in h.request(0).messages[1:]]
    assert roles == [
        ("user", "earlier, by voice"),
        ("assistant", "earlier answer"),
        ("user", "now typed"),
    ]


async def test_a_crash_still_completes_the_turn() -> None:
    h = Harness([Reply("hi")])

    async def broken_sink(event: AgentEvent) -> None:
        h.events.append(event)
        if isinstance(event, TokenDelta) and event.text.strip() == "hi":
            raise RuntimeError("bug")

    out = await h.agent.run_turn(
        TurnInput("sess", "turn_1", "x", "chat", [], h.auth, None), broken_sink
    )
    assert out.outcome == "degraded"
    assert h.of(ErrorEvent)[0].code is ErrorCode.INTERNAL
    assert isinstance(h.events[-1], TurnCompleted)
    assert isinstance(h.events[0], TurnStarted)
