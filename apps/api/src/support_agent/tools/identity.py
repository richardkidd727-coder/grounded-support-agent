"""verify_identity - the gate in front of every account-specific tool."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Annotated, Any, Self

from pydantic import BeforeValidator, Field, StringConstraints, model_validator

from support_agent.crm import repository as repo
from support_agent.crm.policy import MAX_VERIFICATION_ATTEMPTS
from support_agent.tools.base import ErrorKind, Tool, ToolContext, ToolError, ToolInput, ToolOutput

INVOICE_ID_PATTERN = r"(?i)^INV-\d{4}-\d{5}$"
# Models like to typeset ids with non-breaking or en dashes; customers paste them back.
_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212"), "-")


def normalize_invoice_id(value: Any) -> Any:
    return value.translate(_DASHES).strip().upper() if isinstance(value, str) else value


InvoiceId = Annotated[
    str, BeforeValidator(normalize_invoice_id), StringConstraints(pattern=INVOICE_ID_PATTERN)
]


class VerifyIdentityInput(ToolInput):
    email: str = Field(
        min_length=3,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+$",
        description="The email address the customer signs in with.",
    )
    card_last4: str | None = Field(
        default=None, pattern=r"^\d{4}$", description="Last 4 digits of the card on file."
    )
    invoice_id: InvoiceId | None = Field(
        default=None,
        description="An invoice number from the last 12 months (format INV-YYYY-NNNNN).",
    )

    @model_validator(mode="after")
    def _at_least_one_factor(self) -> Self:
        if self.card_last4 is None and self.invoice_id is None:
            raise ValueError("provide card_last4 or invoice_id (or both)")
        return self


class VerifyIdentityOutput(ToolOutput):
    verified: bool
    customer_name: str | None = None
    attempts_remaining: int
    message: str


class VerifyIdentity(Tool[VerifyIdentityInput, VerifyIdentityOutput]):
    name = "verify_identity"
    label = "Verifying identity..."
    description = (
        "Verify who the customer is. Required before discussing account details, looking up the "
        "account, issuing refunds or changing account fields. Needs the sign-in email plus a "
        "second factor: the last 4 digits of the card on file and/or an invoice number from the "
        "last 12 months (every factor given must match). Never ask for full card numbers, "
        "passwords or 2FA codes. After 3 failed attempts verification is locked and you must "
        "create an escalation ticket (category security)."
    )
    input_model = VerifyIdentityInput
    output_model = VerifyIdentityOutput

    async def run(self, args: VerifyIdentityInput, ctx: ToolContext) -> VerifyIdentityOutput:
        auth = ctx.auth
        if auth.failed_attempts >= MAX_VERIFICATION_ATTEMPTS:
            raise ToolError(
                ErrorKind.VERIFICATION_LOCKED,
                "Identity verification is locked after 3 failed attempts in this conversation. "
                "Do not try again. Create an escalation ticket with category security.",
            )

        now = ctx.clock()
        async with ctx.deps.db.engine.connect() as conn:
            customer = await repo.find_customer_by_email(conn, args.email)
            matched = False
            if customer is not None:
                # Every factor supplied must match: giving more can only make it stricter.
                matched = True
                if args.card_last4 is not None:
                    matched = customer.card_last4 is not None and secrets.compare_digest(
                        customer.card_last4, args.card_last4
                    )
                if matched and args.invoice_id is not None:
                    matched = await repo.invoice_belongs_to(
                        conn, args.invoice_id, customer.id, since=now - timedelta(days=365)
                    )

        if customer is None or not matched:
            auth.failed_attempts += 1
            remaining = MAX_VERIFICATION_ATTEMPTS - auth.failed_attempts
            # Same message whether or not the email exists: never confirm account existence.
            message = "The details provided do not match our records."
            if remaining == 0:
                message += " Verification is now locked: create an escalation ticket (security)."
            return VerifyIdentityOutput(
                verified=False, attempts_remaining=remaining, message=message
            )

        auth.customer_id = customer.id
        auth.customer_name = customer.name
        auth.tier = customer.tier
        return VerifyIdentityOutput(
            verified=True,
            customer_name=customer.name,
            attempts_remaining=MAX_VERIFICATION_ATTEMPTS - auth.failed_attempts,
            message=f"Identity verified for {customer.name}.",
        )
