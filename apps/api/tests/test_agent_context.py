"""Conversation window, system prompt and grounding-judge parsing."""

from datetime import date

import pytest

from support_agent.agent.context import (
    TOOL_RESULT_MAX_CHARS,
    StoredMessage,
    estimate_tokens,
    window,
)
from support_agent.agent.grounding import GroundingJudge, parse_verdict
from support_agent.agent.prompts import system_prompt
from support_agent.domain import Tier
from support_agent.llm.fake import FakeLlm, Reply
from support_agent.llm.router import LlmRouter
from support_agent.llm.types import ChatMessage, LlmError, LlmErrorKind, ToolCall
from support_agent.tools.base import SessionAuth


def turn(turn_id: str, user: str, answer: str, *, tool_result: str = '{"ok":true}') -> list:
    call = ToolCall(id=f"{turn_id}-c", name="get_account_profile", raw_arguments="{}")
    return [
        StoredMessage(turn_id, "chat", ChatMessage("user", user)),
        StoredMessage(turn_id, "chat", ChatMessage("assistant", "", (call,))),
        StoredMessage(
            turn_id,
            "chat",
            ChatMessage("tool", tool_result, tool_call_id=call.id, name=call.name),
        ),
        StoredMessage(turn_id, "voice", ChatMessage("assistant", answer)),
    ]


def test_previous_turn_keeps_tool_calls_older_turns_keep_only_words() -> None:
    history = turn("t1", "first q", "first a") + turn("t2", "second q", "second a")
    msgs = window(history, token_budget=10_000)
    assert [(m.role, m.content) for m in msgs] == [
        ("user", "first q"),
        ("assistant", "first a"),
        ("user", "second q"),
        ("assistant", ""),  # carries the tool call
        ("tool", '{"ok":true}'),
        ("assistant", "second a"),
    ]
    assert msgs[3].tool_calls[0].id == "t2-c"


def test_budget_drops_the_oldest_turns_first() -> None:
    history = [m for i in range(10) for m in turn(f"t{i}", f"question {i} " * 20, f"answer {i}")]
    msgs = window(history, token_budget=300)
    users = [m.content for m in msgs if m.role == "user"]
    assert users and users[-1].startswith("question 9")
    assert not any(u.startswith("question 0") for u in users)
    assert sum(estimate_tokens(m) for m in msgs) <= 300


def test_long_tool_results_are_truncated() -> None:
    msgs = window(turn("t1", "q", "a", tool_result="x" * 5000), token_budget=10_000)
    tool = next(m for m in msgs if m.role == "tool")
    assert len(tool.content) <= TOOL_RESULT_MAX_CHARS + 20
    assert tool.content.endswith("(truncated)")


def test_orphaned_tool_calls_and_results_are_dropped() -> None:
    call = ToolCall(id="c1", name="x", raw_arguments="{}")
    history = [
        StoredMessage("t1", "chat", ChatMessage("user", "q")),
        StoredMessage("t1", "chat", ChatMessage("assistant", "checking", (call,))),
        # crashed before the result was stored
        StoredMessage("t1", "chat", ChatMessage("tool", "{}", tool_call_id="ghost", name="x")),
    ]
    msgs = window(history, token_budget=10_000)
    assert [(m.role, m.content, m.tool_calls) for m in msgs] == [
        ("user", "q", ()),
        ("assistant", "checking", ()),
    ]


def test_system_prompt_restates_session_identity_and_channel() -> None:
    verified = SessionAuth(customer_id="cus_a", customer_name="Alice", tier=Tier.PRO)
    text = system_prompt(verified, "voice", date(2026, 11, 25))
    assert "verified as Alice (pro plan)" in text
    assert "spoken aloud" in text and "2026-11-25" in text
    assert "not verified (2 failed" in system_prompt(
        SessionAuth(failed_attempts=2), "chat", date.today()
    )


@pytest.mark.parametrize(
    ("text", "supported", "score", "available"),
    [
        ('{"supported": true, "score": 0.9, "reason": "states it"}', True, 0.9, True),
        ('```json\n{"supported": true, "score": 0.8, "reason": "r"}\n```', True, 0.8, True),
        ('{"supported": true, "score": 0.3, "reason": "weak"}', False, 0.3, True),
        ('{"supported": false, "score": 0.95}', False, 0.95, True),
        ('{"supported": true, "score": 7}', True, 1.0, True),
        ("I think so!", False, 0.0, False),
    ],
)
def test_parse_verdict(text: str, supported: bool, score: float, available: bool) -> None:
    verdict = parse_verdict(text)
    assert (verdict.supported, verdict.score, verdict.available) == (supported, score, available)


async def test_judge_fails_closed_when_no_model_answers() -> None:
    down = FakeLlm([Reply(error=LlmError(LlmErrorKind.UNAVAILABLE, "down"))])
    verdict, result = await GroundingJudge(LlmRouter([down])).judge("q", [{"content": "c"}])
    assert result is None
    assert not verdict.supported and not verdict.available


async def test_judge_sends_only_the_top_excerpts_in_json_mode() -> None:
    judge_llm = FakeLlm([Reply('{"supported": true, "score": 0.9, "reason": "ok"}')])
    excerpts = [
        {"section": f"S{i}", "content": f"text {i}", "similarity": i / 10} for i in range(6)
    ]
    verdict, _ = await GroundingJudge(LlmRouter([judge_llm])).judge("q?", excerpts)
    assert verdict.supported
    request = judge_llm.requests[0]
    assert request.json_output and request.temperature == 0.0
    prompt = request.messages[1].content
    assert "S5" in prompt and "S3" in prompt and "S2" not in prompt  # top 3 by similarity
