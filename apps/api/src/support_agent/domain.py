"""Core domain vocabulary shared by RAG, tools and the orchestrator."""

from enum import StrEnum
from typing import Literal


class Tier(StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


Audience = Literal["all", "free", "pro", "enterprise"]
AUDIENCE_VALUES: frozenset[str] = frozenset({"all", *(t.value for t in Tier)})


def allowed_audiences(tier: Tier | None) -> list[str]:
    """Audiences a caller may read. Unverified callers only see content marked `all`."""
    return ["all"] if tier is None else ["all", tier.value]
