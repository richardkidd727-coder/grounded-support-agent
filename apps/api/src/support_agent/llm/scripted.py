"""A deterministic stand-in for the LLM, for end-to-end tests and offline demos.

`AGENT_LLM_CHAIN=fake:scripted-primary,fake:scripted-backup` swaps the real models for this one.
It reads the conversation the way the real agent would and issues *real* tool calls - verify,
look up the account, refund, search, escalate - so browser tests exercise the actual API,
database, policy engine, guardrails, streaming and traces, with no API key, quota or randomness.
It is not a language model: it only understands the handful of intents the tests use.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from support_agent.llm.types import (
    ChatMessage,
    LlmRequest,
    LlmResult,
    ProviderName,
    StreamItem,
    TextDelta,
    ToolCall,
    Usage,
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CARD = re.compile(r"(?:card|ends? in|ending)\D{0,20}(\d{4})\b", re.IGNORECASE)
_REFUND = re.compile(r"\b(refund|money back|charged)\b", re.IGNORECASE)
_PLAN = re.compile(r"\b(plan|tier|subscription)\b", re.IGNORECASE)
_HUMAN = re.compile(r"\b(human|person|agent|someone)\b", re.IGNORECASE)
# "What is your refund policy?" is a question about policy, not a refund request.
_POLICY_QUESTION = re.compile(
    r"\b(policy|policies|how long|how do|how does|what is|what's|when do)\b", re.IGNORECASE
)
_GREETING = re.compile(r"^\s*(hi|hello|hey|thanks|thank you)\b[\s!.,]*$", re.IGNORECASE)


def _first_sentence(text: str, limit: int = 220) -> str:
    sentence = re.split(r"(?<=[.!?])\s", " ".join(text.split()), maxsplit=1)[0]
    return sentence[:limit]


class ScriptedSupportModel:
    provider: ProviderName = "fake"

    def __init__(self, model: str = "scripted", *, word_delay_s: float = 0.012) -> None:
        self.model = model
        self._delay = word_delay_s

    async def stream(self, request: LlmRequest) -> AsyncIterator[StreamItem]:
        calls: list[tuple[str, dict[str, Any]]] = []
        if request.json_output:  # the grounding judge
            text = json.dumps({"supported": True, "score": 0.8, "reason": "scripted"})
        else:
            text, calls = self._decide(list(request.messages))
        for word in re.findall(r"\S+\s*|\s+", text):
            await asyncio.sleep(self._delay)
            yield TextDelta(word)
        prompt_chars = sum(len(m.content) for m in request.messages)
        yield LlmResult(
            provider=self.provider,
            model=self.model,
            text=text,
            tool_calls=tuple(
                ToolCall(id=f"call_{i}_{name}", name=name, raw_arguments=json.dumps(args))
                for i, (name, args) in enumerate(calls)
            ),
            usage=Usage(input_tokens=prompt_chars // 4, output_tokens=max(len(text) // 4, 8)),
            finish_reason="tool_calls" if calls else "stop",
            latency_ms=len(text.split()) * int(self._delay * 1000) + 5,
            ttft_ms=5 if text else None,
        )

    # --- the "policy" of the fake agent -----------------------------------------------------

    def _decide(self, messages: list[ChatMessage]) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
        last_user = max(i for i, m in enumerate(messages) if m.role == "user")
        user = messages[last_user].content
        system = messages[0].content if messages and messages[0].role == "system" else ""
        verified = "is verified as" in system
        results = [
            (m.name or "", json.loads(m.content or "{}"))
            for m in messages[last_user + 1 :]
            if m.role == "tool"
        ]
        wants_refund = bool(_REFUND.search(user)) and not _POLICY_QUESTION.search(user)

        if not results:
            email, card = _EMAIL.search(user), _CARD.search(user)
            if email and card and not verified:
                return "", [
                    ("verify_identity", {"email": email.group(0), "card_last4": card.group(1)})
                ]
            if wants_refund and not verified:
                return (
                    "I can help with that refund. Please share your account email and the last "
                    "4 digits of the card on file.",
                    [],
                )
            if verified and (wants_refund or _PLAN.search(user)):
                return "", [("get_account_profile", {})]
            if _HUMAN.search(user):
                return "", [
                    (
                        "create_escalation_ticket",
                        {
                            "summary": f"Customer asked for a human: {user}"[:900],
                            "category": "other",
                            "priority": "normal",
                        },
                    )
                ]
            if _GREETING.match(user):
                return "Hi! I'm Drift. How can I help with your Driftwood account today?", []
            return "", [("search_policies", {"query": user[:280]})]

        name, out = results[-1]
        if "error" in out:
            return (
                f"Sorry - {name.replace('_', ' ')} isn't working right now. Would you like me "
                "to open a ticket for our team?",
                [],
            )
        if name == "verify_identity":
            if not out.get("verified"):
                return "Those details don't match our records. Could you double-check them?", []
            if wants_refund or _PLAN.search(user):
                return "", [("get_account_profile", {})]
            return f"Thanks {out.get('customer_name')}, you're verified. How can I help?", []
        if name == "get_account_profile":
            if not wants_refund:
                return (
                    f"You're on the {out.get('tier')} plan and your account is "
                    f"{out.get('account_status')}.",
                    [],
                )
            refundable = [
                inv
                for inv in out.get("recent_invoices", [])
                if inv.get("kind") in ("subscription", "upgrade") and not inv.get("refunded")
            ]
            if not refundable:
                return "I couldn't find a recent charge that can be refunded.", []
            invoice = refundable[0]
            return "", [
                (
                    "issue_refund",
                    {
                        "invoice_id": invoice["invoice_id"],
                        "amount": invoice["amount"],
                        "reason": "goodwill",
                        "note": "Customer asked for a refund of an unused period.",
                    },
                )
            ]
        if name == "issue_refund":
            decision = out.get("decision")
            if decision == "approved":
                return (
                    f"Done - your refund of ${out.get('amount_refunded'):.2f} is approved "
                    f"(refund id {out.get('refund_id')}). {out.get('arrives_in') or ''}".strip(),
                    [],
                )
            if decision == "requires_approval":
                return "", [
                    (
                        "create_escalation_ticket",
                        {
                            "summary": f"Refund needs approval: {out.get('explanation')}"[:900],
                            "category": "refund_approval",
                            "priority": "high",
                        },
                    )
                ]
            return f"I'm sorry, I can't refund that charge: {out.get('explanation')}", []
        if name == "search_policies":
            excerpts = out.get("excerpts") or []
            if excerpts:
                top = excerpts[0]
                return (
                    f"According to our policy ({top.get('section')}): "
                    f"{_first_sentence(str(top.get('content', '')))}",
                    [],
                )
            ticket = out.get("escalation") or {}
            if ticket.get("ticket_id"):
                return (
                    f"I couldn't find a policy that answers this, so I've opened ticket "
                    f"{ticket['ticket_id']}. A specialist will reply "
                    f"{ticket.get('expected_first_response', 'soon')}.",
                    [],
                )
            return "I couldn't find that in our policies.", []
        if name == "create_escalation_ticket":
            return (
                f"I've opened ticket {out.get('ticket_id')} ({out.get('priority')} priority). "
                f"Expect a reply {out.get('expected_first_response')}.",
                [],
            )
        return "Is there anything else I can help with?", []
