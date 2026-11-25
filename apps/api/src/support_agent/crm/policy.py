"""Business rules, enforced in code.

The knowledge base *explains* these policies to the model; this module *enforces* them. The LLM can
ask for anything - `evaluate_refund` decides what actually happens. Every limit here appears in
`knowledge_base/refund-policy.md` / `support-hours-and-sla.md`, and
`tests/test_policy.py::test_policy_documents_match_enforced_limits` fails if they drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from support_agent.crm.models import (
    AccountStatus,
    BillingCycle,
    Contract,
    ContractStatus,
    Customer,
    Invoice,
    InvoiceKind,
    RefundReason,
)
from support_agent.domain import Tier

# --- limits (mirrored in the policy documents) -----------------------------------------------

MAX_CHARGE_AGE_DAYS = 60  # no plan refunds charges older than this
PRO_REFUND_WINDOW_DAYS = 30
FREE_ACCIDENTAL_UPGRADE_WINDOW_DAYS = 7
GOODWILL_COOLDOWN_DAYS = 365  # one goodwill refund per 12 months (Pro)
AGENT_REFUND_LIMIT_CENTS: dict[Tier, int | None] = {
    Tier.FREE: None,  # accidental upgrades are refunded in full
    Tier.PRO: 100_00,
    Tier.ENTERPRISE: 500_00,
}
REFUND_APPROVER: dict[Tier, str] = {
    Tier.FREE: "billing team",
    Tier.PRO: "billing team",
    Tier.ENTERPRISE: "account manager",
}
NON_REFUNDABLE_KINDS = frozenset(
    {InvoiceKind.OVERAGE, InvoiceKind.SETUP_FEE, InvoiceKind.MARKETPLACE}
)
REFUNDABLE_CONTRACT_STATUSES = frozenset({ContractStatus.ACTIVE, ContractStatus.GRACE_PERIOD})
MAX_VERIFICATION_ATTEMPTS = 3


class DenialCode(StrEnum):
    ACCOUNT_SUSPENDED = "account_suspended"
    NON_REFUNDABLE_CHARGE = "non_refundable_charge"
    CHARGE_TOO_OLD = "charge_too_old"
    ALREADY_REFUNDED = "already_refunded"
    OUTSIDE_PLAN_WINDOW = "outside_plan_window"
    FREE_PLAN_NOT_ELIGIBLE = "free_plan_not_eligible"
    GOODWILL_LIMIT_REACHED = "goodwill_limit_reached"
    CONTRACT_NOT_ACTIVE = "contract_not_active"
    EXCEEDS_REFUNDABLE_AMOUNT = "exceeds_refundable_amount"


Outcome = Literal["approved", "denied", "requires_approval"]


@dataclass(frozen=True, slots=True)
class RefundDecision:
    outcome: Outcome
    explanation: str  # plain-English reason, safe to relay to the customer
    max_refundable_cents: int  # most this invoice could be refunded under policy (any approver)
    agent_limit_cents: int | None  # most the assistant may issue on its own
    denial: DenialCode | None = None
    approver: str | None = None  # who must approve when outcome == requires_approval
    notes: list[str] = field(default_factory=list)


def _deny(code: DenialCode, explanation: str, max_cents: int = 0) -> RefundDecision:
    return RefundDecision("denied", explanation, max_cents, None, denial=code)


def prorated_remaining_cents(invoice: Invoice, now: datetime) -> int:
    """Unused share of an annual charge, by days, rounded down to the cent."""
    if invoice.period_start is None or invoice.period_end is None:
        return invoice.remaining_cents
    total = (invoice.period_end - invoice.period_start).total_seconds()
    unused = max((invoice.period_end - now).total_seconds(), 0.0)
    share = unused / total if total > 0 else 0.0
    return max(
        min(int(invoice.amount_cents * share) - invoice.refunded_cents, invoice.remaining_cents), 0
    )


def evaluate_refund(
    *,
    customer: Customer,
    contract: Contract | None,
    invoice: Invoice,
    amount_cents: int,
    reason: RefundReason,
    now: datetime,
) -> RefundDecision:
    """Decide a refund request. Pure function: no I/O, fully deterministic for a given `now`."""
    age = now - invoice.charged_at

    if customer.account_status is AccountStatus.SUSPENDED:
        return _deny(
            DenialCode.ACCOUNT_SUSPENDED,
            "Accounts suspended for a Terms of Service violation are not eligible for refunds.",
        )
    if invoice.kind in NON_REFUNDABLE_KINDS:
        return _deny(
            DenialCode.NON_REFUNDABLE_CHARGE,
            f"{invoice.kind.value.replace('_', ' ').capitalize()} charges are not refundable.",
        )
    if age > timedelta(days=MAX_CHARGE_AGE_DAYS):
        return _deny(
            DenialCode.CHARGE_TOO_OLD,
            f"This charge is {age.days} days old; only charges from the last "
            f"{MAX_CHARGE_AGE_DAYS} days can be refunded.",
        )
    if invoice.remaining_cents <= 0:
        return _deny(DenialCode.ALREADY_REFUNDED, "This invoice has already been fully refunded.")

    tier = customer.tier
    notes: list[str] = []

    if tier is Tier.FREE:
        if invoice.kind is not InvoiceKind.UPGRADE:
            return _deny(
                DenialCode.FREE_PLAN_NOT_ELIGIBLE,
                "Free plan refunds only cover accidental upgrade charges.",
            )
        if age > timedelta(days=FREE_ACCIDENTAL_UPGRADE_WINDOW_DAYS):
            return _deny(
                DenialCode.OUTSIDE_PLAN_WINDOW,
                f"Accidental upgrades can be refunded within {FREE_ACCIDENTAL_UPGRADE_WINDOW_DAYS} "
                f"days; this charge is {age.days} days old. The upgrade can be cancelled instead.",
            )
        max_cents = invoice.remaining_cents
        notes.append("The account returns to the Free plan once the refund is issued.")

    elif tier is Tier.PRO:
        if age > timedelta(days=PRO_REFUND_WINDOW_DAYS):
            return _deny(
                DenialCode.OUTSIDE_PLAN_WINDOW,
                f"Pro charges can be refunded within {PRO_REFUND_WINDOW_DAYS} days; this charge is "
                f"{age.days} days old.",
            )
        last = customer.last_goodwill_refund_at
        if (
            reason is RefundReason.GOODWILL
            and last is not None
            and now - last < timedelta(days=GOODWILL_COOLDOWN_DAYS)
        ):
            return _deny(
                DenialCode.GOODWILL_LIMIT_REACHED,
                "This account already received a goodwill refund in the last 12 months. Refunds "
                "for billing errors made by Driftwood are still possible.",
            )
        is_annual = customer.billing_cycle is BillingCycle.ANNUAL and invoice.period_end is not None
        max_cents = prorated_remaining_cents(invoice, now) if is_annual else invoice.remaining_cents
        if is_annual:
            notes.append("Annual subscriptions are refunded pro rata for the unused days.")

    else:  # enterprise
        if contract is None or contract.status not in REFUNDABLE_CONTRACT_STATUSES:
            status = contract.status.value.replace("_", " ") if contract else "missing"
            return _deny(
                DenialCode.CONTRACT_NOT_ACTIVE,
                f"Refunds need an active contract or one in its grace period; this contract is "
                f"{status}. The customer must renew first.",
            )
        max_cents = invoice.remaining_cents

    agent_limit = AGENT_REFUND_LIMIT_CENTS[tier]
    if amount_cents > max_cents:
        return RefundDecision(
            "denied",
            f"The most that can be refunded on this invoice is ${max_cents / 100:,.2f}.",
            max_cents,
            agent_limit,
            denial=DenialCode.EXCEEDS_REFUNDABLE_AMOUNT,
            notes=notes,
        )
    if agent_limit is not None and amount_cents > agent_limit:
        approver = REFUND_APPROVER[tier]
        return RefundDecision(
            "requires_approval",
            f"Refunds above ${agent_limit / 100:,.0f} need approval from the {approver}. "
            f"Create an escalation ticket with category refund_approval.",
            max_cents,
            agent_limit,
            approver=approver,
            notes=notes,
        )
    return RefundDecision(
        "approved",
        "The refund is within policy.",
        max_cents,
        agent_limit,
        notes=notes,
    )


# --- support response targets (support-hours-and-sla.md) ---------------------------------------

Priority = Literal["low", "normal", "high", "urgent"]

_ENTERPRISE_RESPONSE: dict[str, str] = {
    "urgent": "within 1 hour, 24/7",
    "high": "within 4 hours, 24/7",
    "normal": "within 1 business day",
    "low": "within 2 business days",
}
_PLAN_RESPONSE: dict[Tier | None, str] = {
    None: "within 3 business days",
    Tier.FREE: "within 3 business days",
    Tier.PRO: "within 8 business hours",
}


def expected_first_response(tier: Tier | None, priority: Priority) -> str:
    """Escalation policy: urgent tickets go to on-call immediately regardless of plan."""
    if tier is Tier.ENTERPRISE:
        return _ENTERPRISE_RESPONSE[priority]
    if priority == "urgent":
        return "within 1 hour (routed to the on-call team)"
    return _PLAN_RESPONSE[tier]
