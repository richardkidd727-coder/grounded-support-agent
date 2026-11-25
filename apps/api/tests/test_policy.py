"""Refund policy engine: pure-function tests, one per rule, plus docs <-> code consistency."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from support_agent.config import API_ROOT
from support_agent.crm.models import (
    AccountStatus,
    BillingCycle,
    Contract,
    ContractStatus,
    Customer,
    Invoice,
    InvoiceKind,
    RefundReason,
    SubscriptionStatus,
)
from support_agent.crm.policy import (
    AGENT_REFUND_LIMIT_CENTS,
    FREE_ACCIDENTAL_UPGRADE_WINDOW_DAYS,
    MAX_CHARGE_AGE_DAYS,
    PRO_REFUND_WINDOW_DAYS,
    DenialCode,
    evaluate_refund,
    expected_first_response,
    prorated_remaining_cents,
)
from support_agent.domain import Tier

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def customer(tier: Tier = Tier.PRO, **kw: object) -> Customer:
    base = Customer(
        id="cus_x",
        name="X",
        email="x@example.com",
        billing_email="x@example.com",
        company_name=None,
        contact_phone=None,
        tier=tier,
        account_status=AccountStatus.ACTIVE,
        card_last4="1234",
        subscription_status=SubscriptionStatus.ACTIVE,
        billing_cycle=BillingCycle.MONTHLY,
        seats=1,
        renews_at=None,
        notify_product_updates=True,
        notify_billing_notices=True,
        notify_security_alerts=True,
        last_goodwill_refund_at=None,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def invoice(days_ago: int = 5, amount: int = 45_00, **kw: object) -> Invoice:
    base = Invoice(
        id="INV-2026-00001",
        customer_id="cus_x",
        kind=InvoiceKind.SUBSCRIPTION,
        description="d",
        amount_cents=amount,
        refunded_cents=0,
        currency="USD",
        charged_at=NOW - timedelta(days=days_ago),
        period_start=None,
        period_end=None,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def contract(status: ContractStatus = ContractStatus.ACTIVE) -> Contract:
    return Contract("cus_x", status, date(2026, 1, 1), date(2027, 1, 1), "Priya Shah")


def decide(
    cus: Customer,
    inv: Invoice,
    amount: int,
    reason: RefundReason = RefundReason.GOODWILL,
    con: Contract | None = None,
):  # type: ignore[no-untyped-def]
    return evaluate_refund(
        customer=cus, contract=con, invoice=inv, amount_cents=amount, reason=reason, now=NOW
    )


# --- universal rules -----------------------------------------------------------------------------


def test_pro_refund_within_limit_is_approved() -> None:
    d = decide(customer(), invoice(), 45_00)
    assert d.outcome == "approved"
    assert d.max_refundable_cents == 45_00
    assert d.agent_limit_cents == 100_00


def test_suspended_account_is_denied_before_anything_else() -> None:
    d = decide(customer(account_status=AccountStatus.SUSPENDED), invoice(), 10_00)
    assert (d.outcome, d.denial) == ("denied", DenialCode.ACCOUNT_SUSPENDED)


@pytest.mark.parametrize(
    "kind", [InvoiceKind.OVERAGE, InvoiceKind.SETUP_FEE, InvoiceKind.MARKETPLACE]
)
def test_non_refundable_charge_kinds(kind: InvoiceKind) -> None:
    d = decide(customer(), invoice(kind=kind), 1_00)
    assert d.denial is DenialCode.NON_REFUNDABLE_CHARGE


def test_charges_older_than_60_days_are_never_refundable() -> None:
    d = decide(customer(Tier.ENTERPRISE), invoice(days_ago=61), 1_00, con=contract())
    assert d.denial is DenialCode.CHARGE_TOO_OLD


def test_fully_refunded_invoice() -> None:
    d = decide(customer(), invoice(refunded_cents=45_00), 1_00)
    assert d.denial is DenialCode.ALREADY_REFUNDED


def test_amount_above_remaining_balance_is_denied_with_the_maximum() -> None:
    d = decide(customer(), invoice(refunded_cents=40_00), 10_00)
    assert d.denial is DenialCode.EXCEEDS_REFUNDABLE_AMOUNT
    assert d.max_refundable_cents == 5_00


# --- Pro ---------------------------------------------------------------------------------------


def test_pro_window_boundary() -> None:
    assert decide(customer(), invoice(days_ago=PRO_REFUND_WINDOW_DAYS), 1_00).outcome == "approved"
    late = decide(customer(), invoice(days_ago=PRO_REFUND_WINDOW_DAYS + 1), 1_00)
    assert late.denial is DenialCode.OUTSIDE_PLAN_WINDOW


def test_pro_above_agent_limit_requires_billing_team_approval() -> None:
    d = decide(customer(), invoice(amount=300_00), 150_00)
    assert d.outcome == "requires_approval"
    assert d.approver == "billing team"


def test_goodwill_refund_limited_to_one_per_12_months() -> None:
    recent = customer(last_goodwill_refund_at=NOW - timedelta(days=120))
    assert decide(recent, invoice(), 10_00).denial is DenialCode.GOODWILL_LIMIT_REACHED
    # Billing errors are Driftwood's fault and never count against the limit.
    assert decide(recent, invoice(), 10_00, RefundReason.BILLING_ERROR).outcome == "approved"
    # After a year the customer is eligible again.
    old = customer(last_goodwill_refund_at=NOW - timedelta(days=366))
    assert decide(old, invoice(), 10_00).outcome == "approved"


def test_annual_pro_is_prorated_by_unused_days() -> None:
    inv = invoice(
        days_ago=20,
        amount=720_00,
        period_start=NOW - timedelta(days=20),
        period_end=NOW + timedelta(days=345),
    )
    expected = int(720_00 * 345 / 365)
    assert prorated_remaining_cents(inv, NOW) == expected
    annual = customer(billing_cycle=BillingCycle.ANNUAL)

    full = decide(annual, inv, 720_00)
    assert full.denial is DenialCode.EXCEEDS_REFUNDABLE_AMOUNT
    assert full.max_refundable_cents == expected

    within = decide(annual, inv, 600_00)
    assert within.outcome == "requires_approval"  # prorated amount is above the $100 limit
    assert any("pro rata" in n for n in within.notes)


# --- Free --------------------------------------------------------------------------------------


def test_free_accidental_upgrade_refunded_in_full_within_7_days() -> None:
    free = customer(Tier.FREE, billing_cycle=None)
    d = decide(free, invoice(days_ago=3, amount=15_00, kind=InvoiceKind.UPGRADE), 15_00)
    assert d.outcome == "approved"
    assert d.agent_limit_cents is None  # no cap: the whole accidental charge
    late = decide(
        free,
        invoice(
            days_ago=FREE_ACCIDENTAL_UPGRADE_WINDOW_DAYS + 1, amount=15_00, kind=InvoiceKind.UPGRADE
        ),
        15_00,
    )
    assert late.denial is DenialCode.OUTSIDE_PLAN_WINDOW


def test_free_plan_only_refunds_upgrades() -> None:
    d = decide(customer(Tier.FREE), invoice(), 1_00)
    assert d.denial is DenialCode.FREE_PLAN_NOT_ELIGIBLE


# --- Enterprise ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (ContractStatus.ACTIVE, "approved"),
        (ContractStatus.GRACE_PERIOD, "approved"),
        (ContractStatus.EXPIRED, "denied"),
        (ContractStatus.TERMINATED, "denied"),
    ],
)
def test_enterprise_refunds_follow_contract_status(status: ContractStatus, outcome: str) -> None:
    d = decide(
        customer(Tier.ENTERPRISE),
        invoice(days_ago=50, amount=2_000_00),
        300_00,
        con=contract(status),
    )
    assert d.outcome == outcome
    if outcome == "denied":
        assert d.denial is DenialCode.CONTRACT_NOT_ACTIVE


def test_enterprise_above_500_requires_account_manager() -> None:
    d = decide(customer(Tier.ENTERPRISE), invoice(amount=2_400_00), 900_00, con=contract())
    assert (d.outcome, d.approver) == ("requires_approval", "account manager")


def test_enterprise_without_contract_is_denied() -> None:
    d = decide(customer(Tier.ENTERPRISE), invoice(), 1_00, con=None)
    assert d.denial is DenialCode.CONTRACT_NOT_ACTIVE


# --- SLA ---------------------------------------------------------------------------------------


def test_expected_first_response() -> None:
    assert expected_first_response(Tier.ENTERPRISE, "urgent") == "within 1 hour, 24/7"
    assert expected_first_response(Tier.PRO, "normal") == "within 8 business hours"
    assert expected_first_response(None, "normal") == "within 3 business days"
    assert "on-call" in expected_first_response(Tier.FREE, "urgent")


# --- the documents describe what the code enforces ---------------------------------------------


def test_policy_documents_match_enforced_limits() -> None:
    refund_doc = (API_ROOT / "knowledge_base" / "refund-policy.md").read_text(encoding="utf-8")
    pro_limit = AGENT_REFUND_LIMIT_CENTS[Tier.PRO]
    ent_limit = AGENT_REFUND_LIMIT_CENTS[Tier.ENTERPRISE]
    assert pro_limit is not None and ent_limit is not None
    for phrase in (
        f"up to ${pro_limit // 100}",
        f"up to ${ent_limit // 100}",
        f"last {MAX_CHARGE_AGE_DAYS} days",
        f"within {PRO_REFUND_WINDOW_DAYS} days",
        f"within {FREE_ACCIDENTAL_UPGRADE_WINDOW_DAYS} days",
        "one goodwill refund every 12 months",
    ):
        assert phrase in refund_doc, f"refund-policy.md no longer says {phrase!r}"

    sla_doc = (API_ROOT / "knowledge_base" / "support-hours-and-sla.md").read_text(encoding="utf-8")
    for phrase in ("1 hour, 24/7", "4 hours, 24/7", "8 business hours", "3 business days"):
        assert phrase in sla_doc, f"support-hours-and-sla.md no longer says {phrase!r}"
