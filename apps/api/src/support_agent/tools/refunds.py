"""issue_refund - the model proposes, `evaluate_refund` decides, the database enforces."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field

from support_agent.crm import repository as repo
from support_agent.crm.models import RefundReason, cents_to_dollars, dollars_to_cents
from support_agent.crm.policy import evaluate_refund
from support_agent.tools.base import ErrorKind, Tool, ToolContext, ToolError, ToolInput, ToolOutput
from support_agent.tools.identity import INVOICE_ID_PATTERN


class IssueRefundInput(ToolInput):
    invoice_id: str = Field(pattern=INVOICE_ID_PATTERN, description="Invoice to refund.")
    amount: float = Field(gt=0, le=100_000, description="Amount in the invoice currency.")
    reason: RefundReason = Field(
        description="goodwill (charge was correct), billing_error (Driftwood's mistake), "
        "accidental_upgrade (Free plan), or service_issue."
    )
    note: str = Field(
        min_length=5, max_length=500, description="One-line summary of why, for the audit trail."
    )


class IssueRefundOutput(ToolOutput):
    decision: Literal["approved", "denied", "requires_approval"]
    explanation: str
    refund_id: str | None = None
    amount_refunded: float | None = None
    max_refundable: float
    agent_limit: float | None
    approver: str | None = None
    denial_code: str | None = None
    notes: list[str] = Field(default_factory=list)
    idempotent_replay: bool = False
    arrives_in: str | None = None


def idempotency_key(session_id: str, invoice_id: str, cents: int, reason: RefundReason) -> str:
    raw = f"{session_id}|{invoice_id.upper()}|{cents}|{reason.value}"
    return hashlib.sha256(raw.encode()).hexdigest()


class IssueRefund(Tool[IssueRefundInput, IssueRefundOutput]):
    name = "issue_refund"
    label = "Processing refund..."
    description = (
        "Request a refund on one of the verified customer's invoices. Policy is enforced by the "
        "system, not by you: the result is 'approved' (refund issued), 'denied' (explain the "
        "reason to the customer) or 'requires_approval' (above your limit - create an escalation "
        "ticket with category refund_approval). Call get_account_profile first to see invoices. "
        "Confirm the invoice and amount with the customer before calling."
    )
    input_model = IssueRefundInput
    output_model = IssueRefundOutput
    requires_verification = True

    async def run(self, args: IssueRefundInput, ctx: ToolContext) -> IssueRefundOutput:
        customer_id = ctx.require_customer()
        cents = dollars_to_cents(args.amount)
        key = idempotency_key(ctx.session_id, args.invoice_id, cents, args.reason)
        now = ctx.clock()

        async with ctx.deps.db.engine.begin() as conn:
            existing = await repo.find_refund_by_key(conn, key)
            if existing is not None:  # a retried call: report the original result, refund once
                return IssueRefundOutput(
                    decision="approved",
                    explanation="This refund was already issued.",
                    refund_id=existing.id,
                    amount_refunded=cents_to_dollars(existing.amount_cents),
                    max_refundable=cents_to_dollars(existing.amount_cents),
                    agent_limit=None,
                    idempotent_replay=True,
                    arrives_in="5-10 business days",
                )

            invoice = await repo.lock_invoice(conn, args.invoice_id)
            if invoice is None or invoice.customer_id != customer_id:
                # Same answer for "doesn't exist" and "someone else's": no data leaks.
                raise ToolError(
                    ErrorKind.NOT_FOUND,
                    f"No invoice {args.invoice_id.upper()} on this customer's account. Check "
                    "get_account_profile for valid invoice numbers.",
                )
            customer = await repo.get_customer(conn, customer_id)
            if customer is None:
                raise ToolError(ErrorKind.NOT_FOUND, "The verified account no longer exists.")
            contract = await repo.get_contract(conn, customer_id)

            decision = evaluate_refund(
                customer=customer,
                contract=contract,
                invoice=invoice,
                amount_cents=cents,
                reason=args.reason,
                now=now,
            )
            refund = None
            if decision.outcome == "approved":
                refund = await repo.insert_refund(
                    conn,
                    invoice=invoice,
                    amount_cents=cents,
                    reason=args.reason,
                    note=args.note,
                    idempotency_key=key,
                    session_id=ctx.session_id,
                    now=now,
                )

        return IssueRefundOutput(
            decision=decision.outcome,
            explanation=decision.explanation,
            refund_id=refund.id if refund else None,
            amount_refunded=cents_to_dollars(cents) if refund else None,
            max_refundable=cents_to_dollars(decision.max_refundable_cents),
            agent_limit=(
                cents_to_dollars(decision.agent_limit_cents)
                if decision.agent_limit_cents is not None
                else None
            ),
            approver=decision.approver,
            denial_code=decision.denial.value if decision.denial else None,
            notes=decision.notes,
            arrives_in="5-10 business days" if refund else None,
        )
