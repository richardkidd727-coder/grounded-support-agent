"""Demo helpers: the persona picker shown in the UI (only when DEMO_MODE is on)."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from support_agent.config import Settings
from support_agent.crm.personas import PERSONAS
from support_agent.crm.seed import seed_personas
from support_agent.protection import client_ip, forwarded_chain

router = APIRouter(prefix="/api/demo", tags=["demo"])


class PersonaCard(BaseModel):
    name: str
    email: str
    company: str
    tier: str
    card_last4: str | None
    invoice_id: str | None  # alternative second factor
    scenario: str
    try_saying: str


@router.get("/personas")
async def personas() -> list[PersonaCard]:
    return [
        PersonaCard(
            name=p.name,
            email=p.email,
            company=p.company,
            tier=p.tier.value,
            card_last4=p.card_last4,
            invoice_id=p.invoices[0].id if p.invoices else None,
            scenario=p.scenario,
            try_saying=p.try_saying,
        )
        for p in PERSONAS
    ]


class DemoConfig(BaseModel):
    chaos_enabled: bool  # whether the UI may send an X-Chaos header


@router.get("/config")
async def config(request: Request) -> DemoConfig:
    settings: Settings = request.app.state.settings
    return DemoConfig(chaos_enabled=settings.chaos_enabled)


class ResetResult(BaseModel):
    customers: int


@router.post("/reset")
async def reset(request: Request) -> ResetResult:
    """Restore the demo customers (e.g. Alice's refundable invoice) for another run-through.

    Only the fake CRM is reset; conversations and traces are kept.
    """
    return ResetResult(customers=await seed_personas(request.app.state.db))


class ClientAddress(BaseModel):
    client_ip: str  # what the rate limits key on
    peer: str  # the TCP peer (the host's proxy, when deployed)
    forwarded_for: list[str]  # raw X-Forwarded-For entries, client-supplied ones first
    trusted_proxy_hops: int


@router.get("/client")
async def client(request: Request) -> ClientAddress:
    """Echo how the caller's own address was resolved, so a deploy check can prove that a forged
    X-Forwarded-For does not change it. Only reflects the caller's own request."""
    settings: Settings = request.app.state.settings
    return ClientAddress(
        client_ip=client_ip(request),
        peer=request.client.host if request.client else "unknown",
        forwarded_for=forwarded_chain(request),
        trusted_proxy_hops=settings.trusted_proxy_hops,
    )
