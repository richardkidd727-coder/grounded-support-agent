"""CRM records. Money is always integer cents internally."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from support_agent.domain import Tier


class AccountStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class BillingCycle(StrEnum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


class ContractStatus(StrEnum):
    ACTIVE = "active"
    GRACE_PERIOD = "grace_period"
    EXPIRED = "expired"
    TERMINATED = "terminated"


class InvoiceKind(StrEnum):
    SUBSCRIPTION = "subscription"
    UPGRADE = "upgrade"
    OVERAGE = "overage"
    SETUP_FEE = "setup_fee"
    MARKETPLACE = "marketplace"


class RefundReason(StrEnum):
    GOODWILL = "goodwill"  # charge was correct, customer asks anyway (limited per 12 months)
    BILLING_ERROR = "billing_error"  # Driftwood charged incorrectly
    ACCIDENTAL_UPGRADE = "accidental_upgrade"
    SERVICE_ISSUE = "service_issue"


@dataclass(frozen=True, slots=True)
class Customer:
    id: str
    name: str
    email: str  # login email - used for identity verification, never changed by support
    billing_email: str
    company_name: str | None
    contact_phone: str | None
    tier: Tier
    account_status: AccountStatus
    card_last4: str | None
    subscription_status: SubscriptionStatus
    billing_cycle: BillingCycle | None
    seats: int
    renews_at: datetime | None
    notify_product_updates: bool
    notify_billing_notices: bool
    notify_security_alerts: bool
    last_goodwill_refund_at: datetime | None


@dataclass(frozen=True, slots=True)
class Contract:
    customer_id: str
    status: ContractStatus
    start_date: date
    end_date: date
    account_manager: str


@dataclass(frozen=True, slots=True)
class Invoice:
    id: str
    customer_id: str
    kind: InvoiceKind
    description: str
    amount_cents: int
    refunded_cents: int
    currency: str
    charged_at: datetime
    period_start: datetime | None
    period_end: datetime | None

    @property
    def remaining_cents(self) -> int:
        return self.amount_cents - self.refunded_cents


@dataclass(frozen=True, slots=True)
class Refund:
    id: str
    invoice_id: str
    customer_id: str
    amount_cents: int
    reason: RefundReason
    note: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Ticket:
    id: str
    customer_id: str | None
    summary: str
    category: str
    priority: str
    status: str
    created_at: datetime


def cents_to_dollars(cents: int) -> float:
    return round(cents / 100, 2)


def dollars_to_cents(dollars: float) -> int:
    return round(dollars * 100)
