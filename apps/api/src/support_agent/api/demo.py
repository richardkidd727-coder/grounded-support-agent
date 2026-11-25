"""Demo helpers: the persona picker shown in the UI (only when DEMO_MODE is on)."""

from fastapi import APIRouter
from pydantic import BaseModel

from support_agent.crm.personas import PERSONAS

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
