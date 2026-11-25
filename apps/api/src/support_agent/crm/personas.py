"""Demo customers. Each one exercises a different branch of the policy engine.

Dates are offsets from seed time (not fixed dates), so refund windows stay meaningful no matter
when the demo runs - the API container re-seeds on every boot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from support_agent.crm.models import (
    AccountStatus,
    BillingCycle,
    ContractStatus,
    InvoiceKind,
    SubscriptionStatus,
)
from support_agent.domain import Tier


@dataclass(frozen=True, slots=True)
class InvoiceSeed:
    id: str
    kind: InvoiceKind
    description: str
    amount_cents: int
    days_ago: int
    period_days: int | None = None  # annual invoices: length of the paid period


@dataclass(frozen=True, slots=True)
class ContractSeed:
    status: ContractStatus
    started_days_ago: int
    ends_in_days: int  # negative = already ended
    account_manager: str


@dataclass(frozen=True, slots=True)
class Persona:
    id: str
    name: str
    email: str
    company: str
    tier: Tier
    card_last4: str | None
    scenario: str  # what this persona demonstrates
    try_saying: str  # a prompt that triggers the scenario
    billing_cycle: BillingCycle | None = BillingCycle.MONTHLY
    seats: int = 1
    account_status: AccountStatus = AccountStatus.ACTIVE
    subscription_status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    renews_in_days: int | None = 18
    goodwill_refund_days_ago: int | None = None
    contract: ContractSeed | None = None
    invoices: list[InvoiceSeed] = field(default_factory=list)


PERSONAS: list[Persona] = [
    Persona(
        id="cus_alice",
        name="Alice Martin",
        email="alice@northpeak.example",
        company="Northpeak Studio",
        tier=Tier.PRO,
        card_last4="4242",
        seats=3,
        scenario="Happy path: Pro refund within the $100 agent limit is issued automatically.",
        try_saying="I was charged $45 this month but we stopped using Driftwood. "
        "Can I get a refund?",
        invoices=[
            InvoiceSeed(
                "INV-2026-10231", InvoiceKind.SUBSCRIPTION, "Pro monthly, 3 seats", 45_00, 12
            ),
            InvoiceSeed("INV-2026-09502", InvoiceKind.OVERAGE, "Storage overage", 12_50, 20),
            InvoiceSeed(
                "INV-2026-09877", InvoiceKind.SUBSCRIPTION, "Pro monthly, 3 seats", 45_00, 42
            ),
        ],
    ),
    Persona(
        id="cus_bob",
        name="Bob Chen",
        email="bob@chenlabs.example",
        company="Chen Labs",
        tier=Tier.PRO,
        card_last4="1881",
        billing_cycle=BillingCycle.ANNUAL,
        seats=5,
        renews_in_days=345,
        scenario="Over the limit: a prorated annual refund above $100 needs billing-team approval, "
        "so the agent escalates with a ticket.",
        try_saying="We bought the annual plan three weeks ago and want to cancel and get "
        "our money back.",
        invoices=[
            InvoiceSeed(
                "INV-2026-10012", InvoiceKind.SUBSCRIPTION, "Pro annual, 5 seats", 720_00, 20, 365
            ),
        ],
    ),
    Persona(
        id="cus_carla",
        name="Carla Diaz",
        email="carla@lumenhealth.example",
        company="Lumen Health",
        tier=Tier.ENTERPRISE,
        card_last4=None,  # pays by bank transfer: verifies with an invoice number instead
        billing_cycle=None,
        seats=120,
        renews_in_days=None,
        contract=ContractSeed(ContractStatus.ACTIVE, 200, 165, "Priya Shah"),
        scenario="Enterprise with an active contract: credits up to $500 are issued; "
        "larger ones go "
        "to the account manager. Verifies with an invoice number (no card on file).",
        try_saying="Sync was broken for half a day last week. We'd like a $350 credit on invoice "
        "INV-2026-10107.",
        invoices=[
            InvoiceSeed(
                "INV-2026-10107", InvoiceKind.SUBSCRIPTION, "Seat true-up, 20 seats", 2_400_00, 25
            ),
            InvoiceSeed(
                "INV-2026-08850", InvoiceKind.SETUP_FEE, "Custom domain setup", 500_00, 190
            ),
        ],
    ),
    Persona(
        id="cus_dan",
        name="Dan Okafor",
        email="dan@okafor-logistics.example",
        company="Okafor Logistics",
        tier=Tier.ENTERPRISE,
        card_last4="5100",
        billing_cycle=None,
        seats=60,
        renews_in_days=None,
        contract=ContractSeed(ContractStatus.EXPIRED, 410, -45, "Marcus Lee"),
        scenario="Expired contract: the refund is denied in code even though the charge is recent; "
        "the customer must renew first.",
        try_saying="Please refund $200 from our last invoice.",
        invoices=[
            InvoiceSeed(
                "INV-2026-09915", InvoiceKind.SUBSCRIPTION, "Seat true-up, 10 seats", 1_800_00, 50
            ),
        ],
    ),
    Persona(
        id="cus_erin",
        name="Erin Walsh",
        email="erin@walshdesign.example",
        company="Walsh Design",
        tier=Tier.FREE,
        card_last4="3005",
        billing_cycle=None,
        renews_in_days=None,
        scenario="Free plan accidental upgrade within 7 days: refunded in full.",
        try_saying="I clicked upgrade by mistake and got charged $15. I want to stay on Free.",
        invoices=[
            InvoiceSeed("INV-2026-10290", InvoiceKind.UPGRADE, "Upgrade to Pro, 1 seat", 15_00, 3),
        ],
    ),
    Persona(
        id="cus_frank",
        name="Frank Miller",
        email="frank@millerbros.example",
        company="Miller Bros",
        tier=Tier.PRO,
        card_last4="7777",
        seats=2,
        subscription_status=SubscriptionStatus.PAST_DUE,
        goodwill_refund_days_ago=120,
        scenario="Goodwill limit: already had a goodwill refund this year, so a 'forgot to cancel' "
        "refund is denied - but a billing-error refund is still allowed.",
        try_saying="I forgot to cancel again, can you refund this month's $30?",
        invoices=[
            InvoiceSeed(
                "INV-2026-10175", InvoiceKind.SUBSCRIPTION, "Pro monthly, 2 seats", 30_00, 8
            ),
        ],
    ),
    Persona(
        id="cus_grace",
        name="Grace Kim",
        email="grace@kimventures.example",
        company="Kim Ventures",
        tier=Tier.PRO,
        card_last4="9090",
        account_status=AccountStatus.SUSPENDED,
        scenario="Suspended account (Terms of Service): refunds are blocked.",
        try_saying="Refund my last payment please.",
        invoices=[
            InvoiceSeed(
                "INV-2026-10140", InvoiceKind.SUBSCRIPTION, "Pro monthly, 1 seat", 15_00, 10
            ),
        ],
    ),
    Persona(
        id="cus_hiro",
        name="Hiro Tanaka",
        email="hiro@tanaka.example",
        company="Tanaka Translations",
        tier=Tier.PRO,
        card_last4="2468",
        scenario="Outside the window: a 45-day-old Pro charge and an overage charge are both "
        "refused, with the reason explained.",
        try_saying="Can I get refunds for my last two charges?",
        invoices=[
            InvoiceSeed("INV-2026-10301", InvoiceKind.OVERAGE, "API usage overage", 9_00, 5),
            InvoiceSeed(
                "INV-2026-09650", InvoiceKind.SUBSCRIPTION, "Pro monthly, 1 seat", 15_00, 45
            ),
        ],
    ),
    Persona(
        id="cus_ivy",
        name="Ivy Laurent",
        email="ivy@laurentfinance.example",
        company="Laurent Finance",
        tier=Tier.ENTERPRISE,
        card_last4=None,
        billing_cycle=None,
        seats=75,
        renews_in_days=None,
        contract=ContractSeed(ContractStatus.GRACE_PERIOD, 375, -10, "Priya Shah"),
        scenario="Contract in its grace period: refunds still allowed; good for account updates "
        "(billing email, notification preferences).",
        try_saying="Please change our billing email to accounts@laurentfinance.example.",
        invoices=[
            InvoiceSeed(
                "INV-2026-10055", InvoiceKind.SUBSCRIPTION, "Seat true-up, 5 seats", 600_00, 30
            ),
        ],
    ),
]
