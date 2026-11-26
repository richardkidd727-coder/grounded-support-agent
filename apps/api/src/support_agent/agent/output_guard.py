"""Output guard: catch reference numbers the model invented.

Observed live with two different models: told to escalate, the model *said* it had opened a
ticket and quoted a plausible id ("Ticket ID: INC-20231126-0012") without ever calling the tool.
The customer would wait for a ticket that does not exist. Prompting reduces this but cannot
guarantee it, so the final reply is checked: any ticket/case reference that no tool produced in
this conversation is treated as invented (the orchestrator then opens a real ticket and corrects
the reply).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Hyphen variants models like to typeset ids with (non-breaking, en/em dash, minus).
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2212"
_DASH_CLASS = "-" + _DASHES
_NORMALISE = str.maketrans(dict.fromkeys(_DASHES, "-"))

# A reference = letters, a dash, then digits/letters - e.g. TCK-123456, INC-2023-0012, CASE-42A.
_ID = rf"[A-Z]{{2,6}}[{_DASH_CLASS}][0-9][0-9A-Z{_DASH_CLASS}]{{1,}}"
# ...that the reply presents as a ticket/case/reference, or that uses our own ticket prefix.
_CLAIMED = re.compile(
    rf"(?i:\b(?:ticket|case|reference|incident|request)\b)[^\n]{{0,40}}?\b({_ID})"
)
_OUR_FORMAT = re.compile(rf"\bTCK[{_DASH_CLASS}]\d+\b")


def normalise(ref: str) -> str:
    return ref.translate(_NORMALISE).upper().rstrip(".-")


def references_in(text: str) -> set[str]:
    found = {m.group(1) for m in _CLAIMED.finditer(text)} | set(_OUR_FORMAT.findall(text))
    return {normalise(r) for r in found}


def invented_references(reply: str, evidence: Iterable[str]) -> list[str]:
    """References cited in `reply` that appear nowhere in `evidence` (tool results, history)."""
    known = set()
    for item in evidence:
        known |= {normalise(r) for r in _OUR_FORMAT.findall(item)}
        known |= references_in(item)
    return sorted(r for r in references_in(reply) if r not in known)
