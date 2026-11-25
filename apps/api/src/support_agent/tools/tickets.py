"""create_escalation_ticket - the structured hand-off to a human."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field

from support_agent.crm import repository as repo
from support_agent.crm.policy import expected_first_response
from support_agent.tools.base import Tool, ToolContext, ToolInput, ToolOutput

Category = Literal[
    "refund_approval",
    "billing",
    "technical",
    "security",
    "legal",
    "account",
    "out_of_scope",
    "other",
]
Priority = Literal["low", "normal", "high", "urgent"]


class CreateEscalationTicketInput(ToolInput):
    summary: str = Field(
        min_length=10,
        max_length=1000,
        description="What the customer needs and why it is escalated, for the human agent.",
    )
    category: Category
    priority: Priority = Field(
        description="urgent: security incident, data loss, Enterprise P1. high: refund above "
        "limit, Enterprise P2, legal. normal: customer asked for a human, unanswered policy "
        "question. low: feedback, feature requests."
    )
    contact_preference: Literal["email", "phone", "chat"] = "email"
    attempted_steps: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="What you already tried in this conversation.",
    )


class CreateEscalationTicketOutput(ToolOutput):
    ticket_id: str
    category: str
    priority: str
    status: str
    expected_first_response: str
    idempotent_replay: bool = False


class CreateEscalationTicket(Tool[CreateEscalationTicketInput, CreateEscalationTicketOutput]):
    name = "create_escalation_ticket"
    label = "Escalating to a human agent..."
    description = (
        "Hand the conversation to a human agent. Use it when the customer asks for a person, a "
        "refund needs approval, you are not confident the policies answer the question, "
        "verification is locked, or the issue involves security, data loss or legal matters. "
        "Works for unverified customers too. Tell the customer the ticket id and expected "
        "response time."
    )
    input_model = CreateEscalationTicketInput
    output_model = CreateEscalationTicketOutput

    async def run(
        self, args: CreateEscalationTicketInput, ctx: ToolContext
    ) -> CreateEscalationTicketOutput:
        auth = ctx.auth
        key = hashlib.sha256(
            f"{ctx.session_id}|{args.category}|{' '.join(args.summary.lower().split())}".encode()
        ).hexdigest()
        response = expected_first_response(auth.tier, args.priority)

        async with ctx.deps.db.engine.begin() as conn:
            existing = await repo.find_ticket_by_key(conn, key)
            if existing is not None:
                return CreateEscalationTicketOutput(
                    ticket_id=existing.id,
                    category=existing.category,
                    priority=existing.priority,
                    status=existing.status,
                    expected_first_response=response,
                    idempotent_replay=True,
                )
            ticket = await repo.insert_ticket(
                conn,
                customer_id=auth.customer_id,
                session_id=ctx.session_id,
                summary=args.summary,
                category=args.category,
                priority=args.priority,
                details={
                    "identity_verified": auth.verified,
                    "tier": auth.tier.value if auth.tier else None,
                    "failed_verification_attempts": auth.failed_attempts,
                    "contact_preference": args.contact_preference,
                    "attempted_steps": args.attempted_steps,
                },
                idempotency_key=key,
                now=ctx.clock(),
            )
        return CreateEscalationTicketOutput(
            ticket_id=ticket.id,
            category=ticket.category,
            priority=ticket.priority,
            status=ticket.status,
            expected_first_response=response,
        )
