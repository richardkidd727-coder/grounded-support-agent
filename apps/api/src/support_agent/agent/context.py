"""What the model sees of the conversation so far.

Memory has two layers:

* **Structured session state** - who is verified, their tier - lives server-side and is restated
  in the system prompt every turn, so it can never scroll out of the window.
* **Conversation text** - the most recent turns that fit `history_token_budget`, newest first.
  The previous turn keeps its tool calls and results (the model often refers back to an invoice
  list it just fetched); older turns keep only what was said. Tool chatter is the bulk of the
  tokens, and a free-tier budget of ~8k tokens/minute makes that trade worth it.

Chat and voice turns share one history, so a customer can start typing and continue by voice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from support_agent.events.schema import Mode
from support_agent.llm.types import ChatMessage

TOOL_RESULT_MAX_CHARS = 1500
_CHARS_PER_TOKEN = 3.5


@dataclass(frozen=True, slots=True)
class StoredMessage:
    turn_id: str
    mode: Mode
    message: ChatMessage


def estimate_tokens(message: ChatMessage) -> int:
    chars = len(message.content) + sum(
        len(c.name) + len(c.raw_arguments) for c in message.tool_calls
    )
    return int(chars / _CHARS_PER_TOKEN) + 4


def _group_turns(history: Sequence[StoredMessage]) -> list[list[ChatMessage]]:
    turns: list[list[ChatMessage]] = []
    current: str | None = None
    for item in history:
        if item.turn_id != current:
            turns.append([])
            current = item.turn_id
        turns[-1].append(item.message)
    return turns


def _text_only(turn: list[ChatMessage]) -> list[ChatMessage]:
    """User and assistant words, without tool calls/results."""
    out: list[ChatMessage] = []
    for m in turn:
        if m.role == "user" or (m.role == "assistant" and m.content.strip()):
            out.append(ChatMessage(m.role, m.content))
    return out


def _with_tools(turn: list[ChatMessage]) -> list[ChatMessage]:
    """Full turn, with long tool results truncated and orphaned tool calls dropped."""
    answered = {m.tool_call_id for m in turn if m.role == "tool"}
    out: list[ChatMessage] = []
    for m in turn:
        if m.role == "assistant" and m.tool_calls:
            calls = tuple(c for c in m.tool_calls if c.id in answered)
            if calls or m.content.strip():
                out.append(ChatMessage("assistant", m.content, calls))
        elif m.role == "tool":
            content = m.content
            if len(content) > TOOL_RESULT_MAX_CHARS:
                content = content[:TOOL_RESULT_MAX_CHARS] + "...(truncated)"
            out.append(ChatMessage("tool", content, tool_call_id=m.tool_call_id, name=m.name))
        else:
            out.append(m)
    # A tool result whose call was dropped would be rejected by the API.
    called = {c.id for m in out for c in m.tool_calls}
    return [m for m in out if m.role != "tool" or m.tool_call_id in called]


def window(history: Sequence[StoredMessage], token_budget: int) -> list[ChatMessage]:
    """The prior turns to send, oldest first, within `token_budget`."""
    turns = _group_turns(history)
    selected: list[list[ChatMessage]] = []
    used = 0
    for age, turn in enumerate(reversed(turns)):
        messages = _with_tools(turn) if age == 0 else _text_only(turn)
        cost = sum(estimate_tokens(m) for m in messages)
        if used + cost > token_budget:
            if age == 0:  # always keep at least the words of the previous turn
                messages = _text_only(turn)
                cost = sum(estimate_tokens(m) for m in messages)
                if used + cost > token_budget:
                    break
            else:
                break
        selected.append(messages)
        used += cost
    return [m for turn in reversed(selected) for m in turn]
