"""Three-band retrieval confidence gate (thresholds calibrated by `uv run eval-retrieval`)."""

from typing import Literal

from support_agent.config import Settings

GateBand = Literal["reject", "uncertain", "confident"]


def gate_band(similarity: float, settings: Settings) -> GateBand:
    if similarity < settings.retrieval_reject_below:
        return "reject"
    if similarity >= settings.retrieval_accept_above:
        return "confident"
    return "uncertain"
