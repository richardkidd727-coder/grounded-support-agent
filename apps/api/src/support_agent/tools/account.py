"""get_account_profile and update_account_field."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from support_agent.crm import repository as repo
from support_agent.crm.models import cents_to_dollars
from support_agent.tools.base import ErrorKind, Tool, ToolContext, ToolError, ToolInput, ToolOutput

# --- get_account_profile -----------------------------------------------------------------------


class GetAccountProfileInput(ToolInput):
    pass


class InvoiceSummary(ToolOutput):
    invoice_id: str
    description: str
    kind: str
    amount: float
    refunded: float
    currency: str
    charged_on: str
    days_ago: int


class SubscriptionInfo(ToolOutput):
    status: str
    billing_cycle: str | None
    seats: int
    renews_on: str | None


class ContractInfo(ToolOutput):
    status: str
    start_date: str
    end_date: str
    account_manager: str


class AccountProfile(ToolOutput):
    customer_id: str
    name: str
    company_name: str | None
    tier: str
    account_status: str
    billing_email: str
    subscription: SubscriptionInfo
    contract: ContractInfo | None
    recent_invoices: list[InvoiceSummary]
    open_tickets: int


class GetAccountProfile(Tool[GetAccountProfileInput, AccountProfile]):
    name = "get_account_profile"
    label = "Checking account tier & contract..."
    description = (
        "Look up the verified customer's account: plan tier, account status, subscription, "
        "Enterprise contract status, recent invoices (with amounts already refunded) and open "
        "tickets. Call this before answering account-specific questions or deciding on a refund. "
        "Takes no arguments - it always returns the verified customer's own account."
    )
    input_model = GetAccountProfileInput
    output_model = AccountProfile
    requires_verification = True

    async def run(self, args: GetAccountProfileInput, ctx: ToolContext) -> AccountProfile:
        customer_id = ctx.require_customer()
        now = ctx.clock()
        async with ctx.deps.db.engine.connect() as conn:
            customer = await repo.get_customer(conn, customer_id)
            if customer is None:
                raise ToolError(ErrorKind.NOT_FOUND, "The verified account no longer exists.")
            contract = await repo.get_contract(conn, customer_id)
            invoices = await repo.recent_invoices(conn, customer_id)
            open_tickets = await repo.open_ticket_count(conn, customer_id)

        return AccountProfile(
            customer_id=customer.id,
            name=customer.name,
            company_name=customer.company_name,
            tier=customer.tier.value,
            account_status=customer.account_status.value,
            billing_email=customer.billing_email,
            subscription=SubscriptionInfo(
                status=customer.subscription_status.value,
                billing_cycle=customer.billing_cycle.value if customer.billing_cycle else None,
                seats=customer.seats,
                renews_on=customer.renews_at.date().isoformat() if customer.renews_at else None,
            ),
            contract=(
                ContractInfo(
                    status=contract.status.value,
                    start_date=contract.start_date.isoformat(),
                    end_date=contract.end_date.isoformat(),
                    account_manager=contract.account_manager,
                )
                if contract
                else None
            ),
            recent_invoices=[
                InvoiceSummary(
                    invoice_id=i.id,
                    description=i.description,
                    kind=i.kind.value,
                    amount=cents_to_dollars(i.amount_cents),
                    refunded=cents_to_dollars(i.refunded_cents),
                    currency=i.currency,
                    charged_on=i.charged_at.date().isoformat(),
                    days_ago=(now - i.charged_at).days,
                )
                for i in invoices
            ],
            open_tickets=open_tickets,
        )


# --- update_account_field ------------------------------------------------------------------------

UpdatableField = Literal[
    "billing_email",
    "company_name",
    "contact_phone",
    "notify_product_updates",
    "notify_billing_notices",
    "notify_security_alerts",
]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^\+?[0-9][0-9 ()\-]{6,19}$")
_TRUE = {"on", "true", "yes", "enabled", "enable", "1"}
_FALSE = {"off", "false", "no", "disabled", "disable", "0"}


class UpdateAccountFieldInput(ToolInput):
    field: UpdatableField = Field(description="The account field to change.")
    value: str = Field(
        min_length=1,
        max_length=200,
        description="New value. For notify_* fields use 'on' or 'off'.",
    )


class UpdateAccountFieldOutput(ToolOutput):
    updated: bool
    field: str
    old_value: str | None
    new_value: str | None
    message: str


def _normalise(field: str, raw: str) -> str | bool:
    value = raw.strip()
    if field.startswith("notify_"):
        lowered = value.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ToolError(ErrorKind.INVALID_ARGUMENTS, f"{field} must be 'on' or 'off'.")
    if field == "billing_email":
        if not _EMAIL.match(value):
            raise ToolError(ErrorKind.INVALID_ARGUMENTS, f"{value!r} is not a valid email address.")
        return value.lower()
    if field == "contact_phone":
        if not _PHONE.match(value):
            raise ToolError(ErrorKind.INVALID_ARGUMENTS, f"{value!r} is not a valid phone number.")
        return value
    if not 1 <= len(value) <= 100:
        raise ToolError(ErrorKind.INVALID_ARGUMENTS, "company_name must be 1-100 characters.")
    return value


class UpdateAccountField(Tool[UpdateAccountFieldInput, UpdateAccountFieldOutput]):
    name = "update_account_field"
    label = "Updating account..."
    description = (
        "Change one account detail for the verified customer. Allowed fields only: billing_email, "
        "company_name, contact_phone, notify_product_updates, notify_billing_notices, "
        "notify_security_alerts. The login email, password, 2FA, owner role and workspace "
        "deletion can NOT be changed by support - tell the customer to do those in Settings. "
        "Confirm the new value with the customer before calling. Every change is audited."
    )
    input_model = UpdateAccountFieldInput
    output_model = UpdateAccountFieldOutput
    requires_verification = True

    async def run(
        self, args: UpdateAccountFieldInput, ctx: ToolContext
    ) -> UpdateAccountFieldOutput:
        customer_id = ctx.require_customer()
        value = _normalise(args.field, args.value)

        if args.field == "notify_security_alerts" and value is False:
            return UpdateAccountFieldOutput(
                updated=False,
                field=args.field,
                old_value=None,
                new_value=None,
                message="Security alerts cannot be turned off for account owners.",
            )

        async with ctx.deps.db.engine.begin() as conn:
            customer = await repo.get_customer(conn, customer_id)
            if customer is None:
                raise ToolError(ErrorKind.NOT_FOUND, "The verified account no longer exists.")
            current = getattr(customer, repo.UPDATABLE_COLUMNS[args.field])
            if current == value:
                return UpdateAccountFieldOutput(
                    updated=False,
                    field=args.field,
                    old_value=str(current),
                    new_value=str(value),
                    message=f"{args.field} is already set to that value.",
                )
            old, new = await repo.update_customer_field(
                conn,
                customer_id=customer_id,
                field=args.field,
                value=value,
                session_id=ctx.session_id,
            )
        return UpdateAccountFieldOutput(
            updated=True,
            field=args.field,
            old_value=old,
            new_value=new,
            message=f"{args.field} updated.",
        )
