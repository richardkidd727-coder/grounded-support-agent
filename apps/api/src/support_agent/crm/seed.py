"""Load the demo personas into the CRM tables."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from support_agent.crm.personas import PERSONAS, Persona
from support_agent.crm.repository import wipe
from support_agent.db import Database


async def seed_personas(
    db: Database, *, now: datetime | None = None, personas: Sequence[Persona] = PERSONAS
) -> int:
    """Replace all CRM data with the demo personas, dated relative to `now`."""
    now = now or datetime.now(UTC)
    async with db.engine.begin() as conn:
        await wipe(conn)
        for p in personas:
            await conn.execute(
                text("""
                    INSERT INTO customers
                        (id, name, email, billing_email, company_name, tier, account_status,
                         card_last4, subscription_status, billing_cycle, seats, renews_at,
                         last_goodwill_refund_at, created_at)
                    VALUES (:id, :name, :email, :email, :company, :tier, :account_status,
                            :card, :sub_status, :cycle, :seats, :renews_at, :goodwill, :created)
                """),
                {
                    "id": p.id,
                    "name": p.name,
                    "email": p.email,
                    "company": p.company,
                    "tier": p.tier.value,
                    "account_status": p.account_status.value,
                    "card": p.card_last4,
                    "sub_status": p.subscription_status.value,
                    "cycle": p.billing_cycle.value if p.billing_cycle else None,
                    "seats": p.seats,
                    "renews_at": (
                        now + timedelta(days=p.renews_in_days) if p.renews_in_days else None
                    ),
                    "goodwill": (
                        now - timedelta(days=p.goodwill_refund_days_ago)
                        if p.goodwill_refund_days_ago
                        else None
                    ),
                    "created": now - timedelta(days=400),
                },
            )
            if p.contract:
                c = p.contract
                await conn.execute(
                    text("""
                        INSERT INTO contracts (customer_id, status, start_date, end_date,
                                               account_manager)
                        VALUES (:id, :status, :start, :end, :manager)
                    """),
                    {
                        "id": p.id,
                        "status": c.status.value,
                        "start": (now - timedelta(days=c.started_days_ago)).date(),
                        "end": (now + timedelta(days=c.ends_in_days)).date(),
                        "manager": c.account_manager,
                    },
                )
            for inv in p.invoices:
                charged = now - timedelta(days=inv.days_ago)
                await conn.execute(
                    text("""
                        INSERT INTO invoices (id, customer_id, kind, description, amount_cents,
                                              charged_at, period_start, period_end)
                        VALUES (:id, :cus, :kind, :desc, :amount, :charged, :pstart, :pend)
                    """),
                    {
                        "id": inv.id,
                        "cus": p.id,
                        "kind": inv.kind.value,
                        "desc": inv.description,
                        "amount": inv.amount_cents,
                        "charged": charged,
                        "pstart": charged if inv.period_days else None,
                        "pend": charged + timedelta(days=inv.period_days)
                        if inv.period_days
                        else None,
                    },
                )
    return len(personas)
