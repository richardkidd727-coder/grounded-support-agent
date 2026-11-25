"""mock CRM: customers, contracts, invoices, refunds, tickets, account change audit

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE customers (
            id                       text PRIMARY KEY,
            name                     text NOT NULL,
            email                    text NOT NULL UNIQUE CHECK (email = lower(email)),  -- login
            billing_email            text NOT NULL,  -- support may change this one, never login
            company_name             text,
            contact_phone            text,
            tier                     text NOT NULL CHECK (tier IN ('free', 'pro', 'enterprise')),
            account_status           text NOT NULL
                                     CHECK (account_status IN ('active', 'suspended')),
            card_last4               text CHECK (card_last4 ~ '^[0-9]{4}$'),
            subscription_status      text NOT NULL
                                     CHECK (subscription_status IN
                                            ('active', 'past_due', 'canceled')),
            billing_cycle            text CHECK (billing_cycle IN ('monthly', 'annual')),
            seats                    integer NOT NULL DEFAULT 1 CHECK (seats > 0),
            renews_at                timestamptz,
            notify_product_updates   boolean NOT NULL DEFAULT true,
            notify_billing_notices   boolean NOT NULL DEFAULT true,
            notify_security_alerts   boolean NOT NULL DEFAULT true,
            last_goodwill_refund_at  timestamptz,
            created_at               timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE contracts (
            customer_id      text PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
            status           text NOT NULL
                             CHECK (status IN ('active', 'grace_period', 'expired', 'terminated')),
            start_date       date NOT NULL,
            end_date         date NOT NULL,
            account_manager  text NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE invoices (
            id              text PRIMARY KEY,               -- INV-YYYY-NNNNN
            customer_id     text NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            kind            text NOT NULL CHECK (kind IN
                            ('subscription', 'upgrade', 'overage', 'setup_fee', 'marketplace')),
            description     text NOT NULL,
            amount_cents    integer NOT NULL CHECK (amount_cents > 0),
            -- The database itself guarantees an invoice can never be over-refunded.
            refunded_cents  integer NOT NULL DEFAULT 0
                            CHECK (refunded_cents >= 0 AND refunded_cents <= amount_cents),
            currency        text NOT NULL DEFAULT 'USD',
            charged_at      timestamptz NOT NULL,
            period_start    timestamptz,
            period_end      timestamptz
        )
    """)
    op.execute("CREATE INDEX invoices_customer_idx ON invoices (customer_id, charged_at DESC)")
    op.execute("""
        CREATE TABLE refunds (
            id               text PRIMARY KEY,
            invoice_id       text NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
            customer_id      text NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            amount_cents     integer NOT NULL CHECK (amount_cents > 0),
            reason           text NOT NULL,
            note             text NOT NULL,
            idempotency_key  text NOT NULL UNIQUE,  -- a retried tool call never refunds twice
            session_id       text NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE tickets (
            id               text PRIMARY KEY,      -- TCK-NNNNNN
            customer_id      text REFERENCES customers(id) ON DELETE CASCADE,
            session_id       text NOT NULL,
            summary          text NOT NULL,
            category         text NOT NULL,
            priority         text NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
            status           text NOT NULL DEFAULT 'open',
            details          jsonb NOT NULL DEFAULT '{}'::jsonb,
            idempotency_key  text NOT NULL UNIQUE,
            created_at       timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE account_changes (
            id           bigserial PRIMARY KEY,
            customer_id  text NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            field        text NOT NULL,
            old_value    text,
            new_value    text,
            session_id   text NOT NULL,
            changed_at   timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    for table in ("account_changes", "tickets", "refunds", "invoices", "contracts", "customers"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
