"""Model fallback routing: which failures fall through, cooldowns, budgets, partial output."""

from typing import Any

import pytest

from support_agent.llm.fake import FakeLlm, Reply
from support_agent.llm.router import AttemptFailed, LlmRouter
from support_agent.llm.types import (
    ChatMessage,
    LlmError,
    LlmErrorKind,
    LlmRequest,
    LlmResult,
    TextDelta,
)
from support_agent.tools.chaos import ChaosPlan

REQUEST = LlmRequest(messages=[ChatMessage("user", "hi")])


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def router(*clients: Any, clock: Clock | None = None, max_wait: float = 5.0) -> LlmRouter:
    clock = clock or Clock()
    return LlmRouter(list(clients), max_cooldown_wait_s=max_wait, clock=clock, sleep=clock.sleep)


async def run(r: LlmRouter, **kw: Any) -> list[Any]:
    return [item async for item in r.stream(REQUEST, **kw)]


def err(kind: LlmErrorKind, retry_after: float | None = None) -> Reply:
    return Reply(error=LlmError(kind, "boom", retry_after_s=retry_after))


@pytest.mark.parametrize(
    "kind",
    [
        LlmErrorKind.RATE_LIMITED,
        LlmErrorKind.QUOTA,
        LlmErrorKind.TIMEOUT,
        LlmErrorKind.UNAVAILABLE,
        LlmErrorKind.BAD_OUTPUT,
        LlmErrorKind.AUTH,
    ],
)
async def test_falls_back_on_provider_failures(kind: LlmErrorKind) -> None:
    primary = FakeLlm([err(kind)], model="primary")
    backup = FakeLlm([Reply("from backup")], model="backup")
    items = await run(router(primary, backup))
    failed = [i for i in items if isinstance(i, AttemptFailed)]
    assert [f.error.model for f in failed] == ["primary"]
    assert isinstance(items[-1], LlmResult) and items[-1].model == "backup"


async def test_bad_request_is_our_fault_and_does_not_fall_back() -> None:
    backup = FakeLlm([Reply("unused")], model="backup")
    with pytest.raises(LlmError) as info:
        await run(router(FakeLlm([err(LlmErrorKind.BAD_REQUEST)]), backup))
    assert info.value.kind is LlmErrorKind.BAD_REQUEST
    assert backup.remaining == 1


async def test_never_falls_back_after_text_was_streamed() -> None:
    primary = FakeLlm(
        [Reply("Half an ans", error=LlmError(LlmErrorKind.TIMEOUT, "x"), error_after_text=True)]
    )
    backup = FakeLlm([Reply("unused")], model="backup")
    seen: list[Any] = []
    with pytest.raises(LlmError) as info:
        async for item in router(primary, backup).stream(REQUEST):
            seen.append(item)
    assert info.value.partial
    assert "".join(i.text for i in seen if isinstance(i, TextDelta)) == "Half an ans"
    assert backup.remaining == 1


async def test_rate_limit_cooldown_is_shared_by_later_requests() -> None:
    clock = Clock()
    primary = FakeLlm([err(LlmErrorKind.RATE_LIMITED, retry_after=30), Reply("p")], model="p")
    backup = FakeLlm([Reply("b1"), Reply("b2")], model="b")
    r = router(primary, backup, clock=clock)
    await run(r)
    second = await run(r)  # primary is cooling: straight to the backup, no wasted 429
    assert not any(isinstance(i, AttemptFailed) for i in second)
    assert second[-1].model == "b"
    clock.now += 31
    third = await run(r)
    assert third[-1].model == "p"  # cooled down: back to the preferred model


async def test_waits_once_for_a_short_rate_limit_instead_of_failing() -> None:
    clock = Clock()
    only = FakeLlm([err(LlmErrorKind.RATE_LIMITED, retry_after=2), Reply("ok")], model="only")
    items = await run(router(only, clock=clock))
    assert clock.slept == [2.0]
    assert [i.error.model for i in items if isinstance(i, AttemptFailed)] == ["only"]
    assert items[-1].text == "ok"


async def test_when_every_model_is_rate_limited_the_soonest_is_retried() -> None:
    # The live failure: backup chaos-killed, primary out of tokens-per-minute (Retry-After 7s).
    clock = Clock()
    primary = FakeLlm([err(LlmErrorKind.RATE_LIMITED, retry_after=7), Reply("primary")], model="p")
    backup = FakeLlm([err(LlmErrorKind.RATE_LIMITED, retry_after=9)], model="b")
    items = await run(router(primary, backup, clock=clock, max_wait=12))
    assert clock.slept == [7.0]
    assert items[-1].model == "p"
    assert [i.error.model for i in items if isinstance(i, AttemptFailed)] == ["p", "b"]


async def test_fails_fast_with_retry_after_when_the_wait_is_long() -> None:
    clock = Clock()
    only = FakeLlm([err(LlmErrorKind.RATE_LIMITED, retry_after=40)], model="only")
    r = router(only, clock=clock)
    with pytest.raises(LlmError):
        await run(r)
    with pytest.raises(LlmError) as info:
        await run(r)
    assert info.value.kind is LlmErrorKind.RATE_LIMITED
    assert info.value.retry_after_s == 40.0
    assert clock.slept == []


async def test_skips_a_model_whose_token_budget_cannot_fit_the_request() -> None:
    primary = FakeLlm([Reply("unused")], model="p")
    primary.budget_wait_s = lambda tokens: 30.0  # type: ignore[attr-defined]
    backup = FakeLlm([Reply("b")], model="b")
    items = await run(router(primary, backup))
    assert not any(isinstance(i, AttemptFailed) for i in items)  # skipped, not attempted
    assert items[-1].model == "b"
    assert primary.remaining == 1


async def test_a_budget_limited_model_is_still_tried_before_giving_up() -> None:
    # Our token-bucket estimate says the primary is busy for 30s, and the backup then fails:
    # the estimate may be pessimistic, so try the primary rather than degrade the turn.
    primary = FakeLlm([Reply("primary after all")], model="p")
    primary.budget_wait_s = lambda tokens: 30.0  # type: ignore[attr-defined]
    backup = FakeLlm([err(LlmErrorKind.UNAVAILABLE)], model="b")
    clock = Clock()
    items = await run(router(primary, backup, clock=clock))
    assert [i.error.model for i in items if isinstance(i, AttemptFailed)] == ["b"]
    assert items[-1].text == "primary after all"
    assert clock.slept == []  # no pointless 30s sleep


async def test_the_final_failure_is_raised_not_announced_as_a_fallback() -> None:
    a = FakeLlm([err(LlmErrorKind.UNAVAILABLE)], model="a")
    b = FakeLlm([err(LlmErrorKind.TIMEOUT)], model="b")
    seen: list[Any] = []
    with pytest.raises(LlmError) as info:
        async for item in router(a, b).stream(REQUEST):
            seen.append(item)
    assert [i.error.model for i in seen if isinstance(i, AttemptFailed)] == ["a"]
    assert info.value.model == "b"


async def test_no_models_configured() -> None:
    with pytest.raises(LlmError) as info:
        await run(LlmRouter([]))
    assert info.value.kind is LlmErrorKind.AUTH


async def test_chaos_once_fails_only_the_first_attempt_of_the_first_call() -> None:
    plan = ChaosPlan.parse("llm:rate_limit:once", [])
    assert plan.llm_fault_for_step(1) is None
    primary = FakeLlm([Reply("p")], model="p")
    backup = FakeLlm([Reply("b")], model="b")
    r = router(primary, backup)
    items = await run(r, fault=plan.llm_fault_for_step(0))
    assert [i.error.model for i in items if isinstance(i, AttemptFailed)] == ["p"]
    assert items[-1].model == "b"
    # an injected 429 must not put a real model on cooldown for everyone else
    assert (await run(r))[-1].model == "p"


async def test_chaos_always_exhausts_every_model() -> None:
    plan = ChaosPlan.parse("llm:timeout", [])
    with pytest.raises(LlmError) as info:
        await run(
            router(FakeLlm([Reply("x")]), FakeLlm([Reply("y")], model="b")),
            fault=plan.llm_fault_for_step(3),
        )
    assert info.value.kind is LlmErrorKind.TIMEOUT
