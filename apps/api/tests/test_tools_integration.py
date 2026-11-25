"""Tools against real Postgres, driven through the executor exactly as the agent will call them."""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text

from support_agent.config import API_ROOT, Settings
from support_agent.crm.seed import seed_personas
from support_agent.db import Database
from support_agent.domain import Tier
from support_agent.rag.embeddings import HashingEmbedder
from support_agent.rag.ingest import ingest_directory
from support_agent.rag.retriever import HybridRetriever
from support_agent.rag.store import KnowledgeBaseStore
from support_agent.tools.base import ErrorKind, SessionAuth, ToolContext, ToolDeps
from support_agent.tools.executor import ToolExecutor, ToolOutcome
from support_agent.tools.registry import build_executor

pytestmark = pytest.mark.integration


@pytest.fixture
async def crm(pg_db: Database) -> Database:
    await seed_personas(pg_db)
    return pg_db


@pytest.fixture
def tools() -> ToolExecutor:
    return build_executor(backoff_s=0.001)


def make_ctx(db: Database, session_id: str = "sess-1", **auth: Any) -> ToolContext:
    store = KnowledgeBaseStore(db)
    deps = ToolDeps(db, HybridRetriever(store, HashingEmbedder()), Settings(_env_file=None))  # type: ignore[call-arg]
    return ToolContext(deps=deps, session_id=session_id, auth=SessionAuth(**auth))


async def verified(db: Database, tools: ToolExecutor, email: str, **factor: str) -> ToolContext:
    ctx = make_ctx(db)
    out = await tools.execute("verify_identity", {"email": email, **factor}, ctx)
    assert out.ok and out.output and out.output["verified"], out
    return ctx


def output(outcome: ToolOutcome) -> dict[str, Any]:
    assert outcome.ok, outcome.error
    assert outcome.output is not None
    return outcome.output


# --- verify_identity -----------------------------------------------------------------------------


async def test_verify_with_card_sets_session_identity(crm: Database, tools: ToolExecutor) -> None:
    ctx = await verified(crm, tools, "Alice@Northpeak.example", card_last4="4242")
    assert (ctx.auth.customer_id, ctx.auth.tier) == ("cus_alice", Tier.PRO)


async def test_verify_with_invoice_number(crm: Database, tools: ToolExecutor) -> None:
    ctx = await verified(crm, tools, "carla@lumenhealth.example", invoice_id="inv-2026-10107")
    assert ctx.auth.tier is Tier.ENTERPRISE


async def test_failed_verification_does_not_reveal_whether_account_exists(
    crm: Database, tools: ToolExecutor
) -> None:
    ctx = make_ctx(crm)
    wrong_card = output(
        await tools.execute(
            "verify_identity", {"email": "alice@northpeak.example", "card_last4": "0000"}, ctx
        )
    )
    no_account = output(
        await tools.execute(
            "verify_identity", {"email": "nobody@nowhere.example", "card_last4": "4242"}, ctx
        )
    )
    assert wrong_card["message"] == no_account["message"]
    assert not wrong_card["verified"] and ctx.auth.customer_id is None


async def test_someone_elses_invoice_does_not_verify(crm: Database, tools: ToolExecutor) -> None:
    ctx = make_ctx(crm)
    out = output(
        await tools.execute(
            "verify_identity",
            {"email": "alice@northpeak.example", "invoice_id": "INV-2026-10012"},  # Bob's
            ctx,
        )
    )
    assert not out["verified"]


async def test_verification_locks_after_three_failures(crm: Database, tools: ToolExecutor) -> None:
    ctx = make_ctx(crm)
    bad = {"email": "alice@northpeak.example", "card_last4": "1111"}
    remaining = [
        output(await tools.execute("verify_identity", bad, ctx))["attempts_remaining"]
        for _ in range(3)
    ]
    assert remaining == [2, 1, 0]
    locked = await tools.execute(
        "verify_identity", {"email": "alice@northpeak.example", "card_last4": "4242"}, ctx
    )
    assert locked.error and locked.error.kind is ErrorKind.VERIFICATION_LOCKED
    assert ctx.auth.customer_id is None  # even the right answer is refused once locked


@pytest.mark.parametrize(
    "args",
    [
        {"email": "a@b.example"},  # no second factor
        {"email": "a@b.example", "card_last4": "4242", "invoice_id": "INV-2026-10231"},  # both
        {"email": "a@b.example", "card_last4": "42"},
        {"email": "not-an-email", "card_last4": "4242"},
    ],
)
async def test_verify_argument_validation(
    crm: Database, tools: ToolExecutor, args: dict[str, str]
) -> None:
    out = await tools.execute("verify_identity", args, make_ctx(crm))
    assert out.error and out.error.kind is ErrorKind.INVALID_ARGUMENTS


# --- get_account_profile -------------------------------------------------------------------------


async def test_account_profile_requires_verification(crm: Database, tools: ToolExecutor) -> None:
    out = await tools.execute("get_account_profile", {}, make_ctx(crm))
    assert out.error and out.error.kind is ErrorKind.NOT_VERIFIED


async def test_account_profile_contents(crm: Database, tools: ToolExecutor) -> None:
    ctx = await verified(crm, tools, "dan@okafor-logistics.example", card_last4="5100")
    profile = output(await tools.execute("get_account_profile", {}, ctx))
    assert profile["tier"] == "enterprise"
    assert profile["contract"]["status"] == "expired"
    assert profile["recent_invoices"][0]["invoice_id"] == "INV-2026-09915"
    assert "card_last4" not in str(profile) and "4242" not in str(profile)  # no secrets returned


# --- issue_refund: one test per persona scenario ------------------------------------------------


async def refund(
    tools: ToolExecutor, ctx: ToolContext, invoice: str, amount: float, reason: str = "goodwill"
) -> dict[str, Any]:
    return output(
        await tools.execute(
            "issue_refund",
            {"invoice_id": invoice, "amount": amount, "reason": reason, "note": "customer asked"},
            ctx,
        )
    )


async def test_alice_refund_is_issued_and_recorded(crm: Database, tools: ToolExecutor) -> None:
    ctx = await verified(crm, tools, "alice@northpeak.example", card_last4="4242")
    out = await refund(tools, ctx, "INV-2026-10231", 45)
    assert out["decision"] == "approved" and out["refund_id"].startswith("re_")
    async with crm.engine.connect() as conn:
        refunded = (
            await conn.execute(
                text("SELECT refunded_cents FROM invoices WHERE id='INV-2026-10231'")
            )
        ).scalar_one()
    assert refunded == 45_00


async def test_retried_refund_is_not_applied_twice(crm: Database, tools: ToolExecutor) -> None:
    ctx = await verified(crm, tools, "alice@northpeak.example", card_last4="4242")
    first = await refund(tools, ctx, "INV-2026-10231", 20)
    second = await refund(tools, ctx, "INV-2026-10231", 20)
    assert second["idempotent_replay"] and second["refund_id"] == first["refund_id"]
    async with crm.engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM refunds"))).scalar_one()
    assert count == 1


async def test_concurrent_refunds_cannot_over_refund(crm: Database, tools: ToolExecutor) -> None:
    # Two sessions race for the same $45 invoice; the row lock serialises them. billing_error is
    # used so the goodwill-per-year rule doesn't decide the second request first.
    ctx_a = await verified(crm, tools, "alice@northpeak.example", card_last4="4242")
    ctx_b = await verified(crm, tools, "alice@northpeak.example", card_last4="4242")
    ctx_b.session_id = "sess-2"
    results = await asyncio.gather(
        refund(tools, ctx_a, "INV-2026-10231", 30, "billing_error"),
        refund(tools, ctx_b, "INV-2026-10231", 30, "billing_error"),
    )
    assert sorted(r["decision"] for r in results) == ["approved", "denied"]
    loser = next(r for r in results if r["decision"] == "denied")
    assert (loser["denial_code"], loser["max_refundable"]) == ("exceeds_refundable_amount", 15.0)
    async with crm.engine.connect() as conn:
        refunded = (
            await conn.execute(
                text("SELECT refunded_cents FROM invoices WHERE id='INV-2026-10231'")
            )
        ).scalar_one()
    assert refunded == 30_00


async def test_bob_annual_refund_needs_approval(crm: Database, tools: ToolExecutor) -> None:
    ctx = await verified(crm, tools, "bob@chenlabs.example", card_last4="1881")
    out = await refund(tools, ctx, "INV-2026-10012", 500)
    assert out["decision"] == "requires_approval" and out["approver"] == "billing team"
    assert 600 < out["max_refundable"] < 720  # prorated
    assert out["refund_id"] is None


async def test_carla_enterprise_credit(crm: Database, tools: ToolExecutor) -> None:
    ctx = await verified(crm, tools, "carla@lumenhealth.example", invoice_id="INV-2026-10107")
    assert (await refund(tools, ctx, "INV-2026-10107", 350, "service_issue"))[
        "decision"
    ] == "approved"
    big = await refund(tools, ctx, "INV-2026-10107", 900, "service_issue")
    assert big["decision"] == "requires_approval" and big["approver"] == "account manager"


@pytest.mark.parametrize(
    ("email", "factor", "invoice", "amount", "reason", "denial"),
    [
        (
            "dan@okafor-logistics.example",
            {"card_last4": "5100"},
            "INV-2026-09915",
            200,
            "goodwill",
            "contract_not_active",
        ),
        (
            "frank@millerbros.example",
            {"card_last4": "7777"},
            "INV-2026-10175",
            30,
            "goodwill",
            "goodwill_limit_reached",
        ),
        (
            "grace@kimventures.example",
            {"card_last4": "9090"},
            "INV-2026-10140",
            15,
            "goodwill",
            "account_suspended",
        ),
        (
            "hiro@tanaka.example",
            {"card_last4": "2468"},
            "INV-2026-09650",
            15,
            "goodwill",
            "outside_plan_window",
        ),
        (
            "hiro@tanaka.example",
            {"card_last4": "2468"},
            "INV-2026-10301",
            9,
            "billing_error",
            "non_refundable_charge",
        ),
        (
            "alice@northpeak.example",
            {"card_last4": "4242"},
            "INV-2026-10231",
            46,
            "goodwill",
            "exceeds_refundable_amount",
        ),
    ],
)
async def test_policy_denials(
    crm: Database,
    tools: ToolExecutor,
    email: str,
    factor: dict[str, str],
    invoice: str,
    amount: float,
    reason: str,
    denial: str,
) -> None:
    ctx = await verified(crm, tools, email, **factor)
    out = await refund(tools, ctx, invoice, amount, reason)
    assert (out["decision"], out["denial_code"]) == ("denied", denial)
    assert out["explanation"]


async def test_frank_billing_error_refund_still_allowed(crm: Database, tools: ToolExecutor) -> None:
    ctx = await verified(crm, tools, "frank@millerbros.example", card_last4="7777")
    assert (await refund(tools, ctx, "INV-2026-10175", 30, "billing_error"))[
        "decision"
    ] == "approved"


async def test_erin_accidental_upgrade(crm: Database, tools: ToolExecutor) -> None:
    ctx = await verified(crm, tools, "erin@walshdesign.example", card_last4="3005")
    out = await refund(tools, ctx, "INV-2026-10290", 15, "accidental_upgrade")
    assert out["decision"] == "approved" and out["notes"]


async def test_cannot_refund_another_customers_invoice(crm: Database, tools: ToolExecutor) -> None:
    ctx = await verified(crm, tools, "alice@northpeak.example", card_last4="4242")
    out = await tools.execute(
        "issue_refund",
        {"invoice_id": "INV-2026-10012", "amount": 10, "reason": "goodwill", "note": "x" * 5},
        ctx,
    )
    assert out.error and out.error.kind is ErrorKind.NOT_FOUND


# --- update_account_field -----------------------------------------------------------------------


async def test_update_billing_email_is_audited_and_login_email_untouched(
    crm: Database, tools: ToolExecutor
) -> None:
    ctx = await verified(crm, tools, "ivy@laurentfinance.example", invoice_id="INV-2026-10055")
    out = output(
        await tools.execute(
            "update_account_field",
            {"field": "billing_email", "value": "Accounts@LaurentFinance.example"},
            ctx,
        )
    )
    assert out["updated"] and out["new_value"] == "accounts@laurentfinance.example"
    async with crm.engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT email, billing_email FROM customers WHERE id='cus_ivy'")
            )
        ).one()
        audit = (await conn.execute(text("SELECT field, old_value FROM account_changes"))).one()
    assert row.email == "ivy@laurentfinance.example"  # login email unchanged
    assert row.billing_email == "accounts@laurentfinance.example"
    assert (audit.field, audit.old_value) == ("billing_email", "ivy@laurentfinance.example")


@pytest.mark.parametrize(
    ("args", "kind"),
    [
        ({"field": "email", "value": "x@y.example"}, ErrorKind.INVALID_ARGUMENTS),  # login email
        ({"field": "password", "value": "hunter2"}, ErrorKind.INVALID_ARGUMENTS),
        ({"field": "billing_email", "value": "not-an-email"}, ErrorKind.INVALID_ARGUMENTS),
        ({"field": "contact_phone", "value": "call me"}, ErrorKind.INVALID_ARGUMENTS),
        ({"field": "notify_product_updates", "value": "maybe"}, ErrorKind.INVALID_ARGUMENTS),
    ],
)
async def test_update_rejects_disallowed_fields_and_bad_values(
    crm: Database, tools: ToolExecutor, args: dict[str, str], kind: ErrorKind
) -> None:
    ctx = await verified(crm, tools, "alice@northpeak.example", card_last4="4242")
    out = await tools.execute("update_account_field", args, ctx)
    assert out.error and out.error.kind is kind


async def test_security_alerts_cannot_be_disabled(crm: Database, tools: ToolExecutor) -> None:
    ctx = await verified(crm, tools, "alice@northpeak.example", card_last4="4242")
    out = output(
        await tools.execute(
            "update_account_field", {"field": "notify_security_alerts", "value": "off"}, ctx
        )
    )
    assert not out["updated"] and "cannot" in out["message"]
    assert output(
        await tools.execute(
            "update_account_field", {"field": "notify_product_updates", "value": "off"}, ctx
        )
    )["updated"]


# --- create_escalation_ticket ------------------------------------------------------------------


async def test_ticket_for_unverified_customer_and_idempotency(
    crm: Database, tools: ToolExecutor
) -> None:
    ctx = make_ctx(crm)
    args = {
        "summary": "Customer asks to speak to a human about a contract question.",
        "category": "account",
        "priority": "normal",
    }
    first = output(await tools.execute("create_escalation_ticket", args, ctx))
    again = output(await tools.execute("create_escalation_ticket", args, ctx))
    assert first["ticket_id"].startswith("TCK-")
    assert again["ticket_id"] == first["ticket_id"] and again["idempotent_replay"]
    assert first["expected_first_response"] == "within 3 business days"


async def test_ticket_carries_verified_context(crm: Database, tools: ToolExecutor) -> None:
    ctx = await verified(crm, tools, "bob@chenlabs.example", card_last4="1881")
    out = output(
        await tools.execute(
            "create_escalation_ticket",
            {
                "summary": "Prorated annual refund of $680 needs billing-team approval.",
                "category": "refund_approval",
                "priority": "high",
                "attempted_steps": ["issue_refund -> requires_approval"],
            },
            ctx,
        )
    )
    assert out["expected_first_response"] == "within 8 business hours"
    async with crm.engine.connect() as conn:
        row = (await conn.execute(text("SELECT customer_id, details FROM tickets"))).one()
    assert row.customer_id == "cus_bob"
    assert row.details["identity_verified"] is True and row.details["tier"] == "pro"


# --- search_policies ------------------------------------------------------------------------------


async def test_search_policies_uses_session_tier_not_arguments(
    crm: Database, tools: ToolExecutor
) -> None:
    await ingest_directory(KnowledgeBaseStore(crm), HashingEmbedder(), API_ROOT / "knowledge_base")

    ctx = await verified(crm, tools, "carla@lumenhealth.example", invoice_id="INV-2026-10107")
    ent = output(
        await tools.execute("search_policies", {"query": "maximum refund support can issue"}, ctx)
    )
    assert ent["audience"] == "enterprise"

    anonymous = output(
        await tools.execute(
            "search_policies", {"query": "maximum refund support can issue"}, make_ctx(crm)
        )
    )
    assert anonymous["audience"] == "general"
    assert all("Enterprise plan" not in e["section"] for e in anonymous["excerpts"])

    # The model cannot widen the audience by passing a tier.
    sneaky = await tools.execute(
        "search_policies", {"query": "refund", "tier": "enterprise"}, make_ctx(crm)
    )
    assert sneaky.error and sneaky.error.kind is ErrorKind.INVALID_ARGUMENTS
