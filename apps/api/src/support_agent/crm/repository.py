"""CRM data access. Functions take an `AsyncConnection` so callers control the transaction."""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from support_agent.crm.models import (
    AccountStatus,
    BillingCycle,
    Contract,
    ContractStatus,
    Customer,
    Invoice,
    InvoiceKind,
    Refund,
    RefundReason,
    SubscriptionStatus,
    Ticket,
)
from support_agent.domain import Tier

# Fields support may change on a customer's behalf (account-management.md). Maps the tool's
# field name to the column - the column name never comes from model output.
UPDATABLE_COLUMNS: dict[str, str] = {
    "billing_email": "billing_email",  # NOT the login email, which only the owner can change
    "company_name": "company_name",
    "contact_phone": "contact_phone",
    "notify_product_updates": "notify_product_updates",
    "notify_billing_notices": "notify_billing_notices",
    "notify_security_alerts": "notify_security_alerts",
}


def _customer(r: Row[Any]) -> Customer:
    return Customer(
        id=r.id,
        name=r.name,
        email=r.email,
        billing_email=r.billing_email,
        company_name=r.company_name,
        contact_phone=r.contact_phone,
        tier=Tier(r.tier),
        account_status=AccountStatus(r.account_status),
        card_last4=r.card_last4,
        subscription_status=SubscriptionStatus(r.subscription_status),
        billing_cycle=BillingCycle(r.billing_cycle) if r.billing_cycle else None,
        seats=r.seats,
        renews_at=r.renews_at,
        notify_product_updates=r.notify_product_updates,
        notify_billing_notices=r.notify_billing_notices,
        notify_security_alerts=r.notify_security_alerts,
        last_goodwill_refund_at=r.last_goodwill_refund_at,
    )


def _invoice(r: Row[Any]) -> Invoice:
    return Invoice(
        id=r.id,
        customer_id=r.customer_id,
        kind=InvoiceKind(r.kind),
        description=r.description,
        amount_cents=r.amount_cents,
        refunded_cents=r.refunded_cents,
        currency=r.currency,
        charged_at=r.charged_at,
        period_start=r.period_start,
        period_end=r.period_end,
    )


# --- reads ---------------------------------------------------------------------------------------


async def get_customer(conn: AsyncConnection, customer_id: str) -> Customer | None:
    row = (
        await conn.execute(text("SELECT * FROM customers WHERE id = :id"), {"id": customer_id})
    ).first()
    return _customer(row) if row else None


async def find_customer_by_email(conn: AsyncConnection, email: str) -> Customer | None:
    row = (
        await conn.execute(
            text("SELECT * FROM customers WHERE email = :email"), {"email": email.strip().lower()}
        )
    ).first()
    return _customer(row) if row else None


async def get_contract(conn: AsyncConnection, customer_id: str) -> Contract | None:
    row = (
        await conn.execute(
            text("SELECT * FROM contracts WHERE customer_id = :id"), {"id": customer_id}
        )
    ).first()
    if not row:
        return None
    return Contract(
        customer_id=row.customer_id,
        status=ContractStatus(row.status),
        start_date=row.start_date,
        end_date=row.end_date,
        account_manager=row.account_manager,
    )


async def recent_invoices(conn: AsyncConnection, customer_id: str, limit: int = 6) -> list[Invoice]:
    rows = await conn.execute(
        text(
            "SELECT * FROM invoices WHERE customer_id = :id ORDER BY charged_at DESC LIMIT :limit"
        ),
        {"id": customer_id, "limit": limit},
    )
    return [_invoice(r) for r in rows]


async def invoice_belongs_to(
    conn: AsyncConnection, invoice_id: str, customer_id: str, since: datetime
) -> bool:
    row = (
        await conn.execute(
            text(
                "SELECT 1 FROM invoices WHERE id = :inv AND customer_id = :cus "
                "AND charged_at >= :since"
            ),
            {"inv": invoice_id.strip().upper(), "cus": customer_id, "since": since},
        )
    ).first()
    return row is not None


async def lock_invoice(conn: AsyncConnection, invoice_id: str) -> Invoice | None:
    """Row-lock the invoice for the rest of the transaction (serialises concurrent refunds)."""
    row = (
        await conn.execute(
            text("SELECT * FROM invoices WHERE id = :id FOR UPDATE"),
            {"id": invoice_id.strip().upper()},
        )
    ).first()
    return _invoice(row) if row else None


async def open_ticket_count(conn: AsyncConnection, customer_id: str) -> int:
    value = (
        await conn.execute(
            text("SELECT count(*) FROM tickets WHERE customer_id = :id AND status = 'open'"),
            {"id": customer_id},
        )
    ).scalar_one()
    return int(value)


async def find_refund_by_key(conn: AsyncConnection, key: str) -> Refund | None:
    row = (
        await conn.execute(text("SELECT * FROM refunds WHERE idempotency_key = :k"), {"k": key})
    ).first()
    if not row:
        return None
    return Refund(
        id=row.id,
        invoice_id=row.invoice_id,
        customer_id=row.customer_id,
        amount_cents=row.amount_cents,
        reason=RefundReason(row.reason),
        note=row.note,
        created_at=row.created_at,
    )


async def find_ticket_by_key(conn: AsyncConnection, key: str) -> Ticket | None:
    row = (
        await conn.execute(text("SELECT * FROM tickets WHERE idempotency_key = :k"), {"k": key})
    ).first()
    if not row:
        return None
    return Ticket(
        id=row.id,
        customer_id=row.customer_id,
        summary=row.summary,
        category=row.category,
        priority=row.priority,
        status=row.status,
        created_at=row.created_at,
    )


# --- writes --------------------------------------------------------------------------------------


async def insert_refund(
    conn: AsyncConnection,
    *,
    invoice: Invoice,
    amount_cents: int,
    reason: RefundReason,
    note: str,
    idempotency_key: str,
    session_id: str,
    now: datetime,
) -> Refund:
    refund_id = f"re_{secrets.token_hex(6)}"
    await conn.execute(
        text("""
            INSERT INTO refunds
                (id, invoice_id, customer_id, amount_cents, reason, note, idempotency_key,
                 session_id, created_at)
            VALUES (:id, :inv, :cus, :amount, :reason, :note, :key, :session, :now)
        """),
        {
            "id": refund_id,
            "inv": invoice.id,
            "cus": invoice.customer_id,
            "amount": amount_cents,
            "reason": reason.value,
            "note": note,
            "key": idempotency_key,
            "session": session_id,
            "now": now,
        },
    )
    await conn.execute(
        text("UPDATE invoices SET refunded_cents = refunded_cents + :amount WHERE id = :id"),
        {"amount": amount_cents, "id": invoice.id},
    )
    if reason is RefundReason.GOODWILL:
        await conn.execute(
            text("UPDATE customers SET last_goodwill_refund_at = :now WHERE id = :id"),
            {"now": now, "id": invoice.customer_id},
        )
    return Refund(refund_id, invoice.id, invoice.customer_id, amount_cents, reason, note, now)


async def update_customer_field(
    conn: AsyncConnection,
    *,
    customer_id: str,
    field: str,
    value: str | bool | None,
    session_id: str,
) -> tuple[str | None, str | None]:
    """Update one allow-listed field and write an audit row. Returns (old, new) as text."""
    column = UPDATABLE_COLUMNS[field]  # KeyError = programming error, never user input
    old = (
        await conn.execute(
            text(f"SELECT {column} FROM customers WHERE id = :id FOR UPDATE"),  # noqa: S608
            {"id": customer_id},
        )
    ).scalar_one()
    await conn.execute(
        text(f"UPDATE customers SET {column} = :value WHERE id = :id"),  # noqa: S608
        {"value": value, "id": customer_id},
    )
    old_text = None if old is None else str(old)
    new_text = None if value is None else str(value)
    await conn.execute(
        text("""
            INSERT INTO account_changes (customer_id, field, old_value, new_value, session_id)
            VALUES (:cus, :field, :old, :new, :session)
        """),
        {
            "cus": customer_id,
            "field": field,
            "old": old_text,
            "new": new_text,
            "session": session_id,
        },
    )
    return old_text, new_text


async def insert_ticket(
    conn: AsyncConnection,
    *,
    customer_id: str | None,
    session_id: str,
    summary: str,
    category: str,
    priority: str,
    details: dict[str, Any],
    idempotency_key: str,
    now: datetime,
) -> Ticket:
    ticket_id = f"TCK-{secrets.randbelow(900_000) + 100_000}"
    await conn.execute(
        text("""
            INSERT INTO tickets
                (id, customer_id, session_id, summary, category, priority, details,
                 idempotency_key, created_at)
            VALUES (:id, :cus, :session, :summary, :category, :priority,
                    CAST(:details AS jsonb), :key, :now)
        """),
        {
            "id": ticket_id,
            "cus": customer_id,
            "session": session_id,
            "summary": summary,
            "category": category,
            "priority": priority,
            "details": json.dumps(details),
            "key": idempotency_key,
            "now": now,
        },
    )
    return Ticket(ticket_id, customer_id, summary, category, priority, "open", now)


async def wipe(conn: AsyncConnection) -> None:
    """Remove all CRM data (cascades to every dependent table)."""
    await conn.execute(text("TRUNCATE customers CASCADE"))
