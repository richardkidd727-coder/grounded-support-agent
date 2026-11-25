"""ToolExecutor behaviour with stub tools: no database needed."""

from typing import Any

import pytest

from support_agent.config import Settings
from support_agent.tools.base import (
    ErrorKind,
    SessionAuth,
    Tool,
    ToolContext,
    ToolDeps,
    ToolError,
    ToolInput,
    ToolOutput,
)
from support_agent.tools.chaos import ChaosPlan, ChaosPlanError
from support_agent.tools.executor import ToolExecutor
from support_agent.tools.registry import default_tools


class EchoIn(ToolInput):
    text: str


class EchoOut(ToolOutput):
    text: str


class Echo(Tool[EchoIn, EchoOut]):
    name = "echo"
    label = "Echoing..."
    description = "Echo text back."
    input_model = EchoIn
    output_model = EchoOut

    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.failures = list(failures or [])
        self.calls = 0

    async def run(self, args: EchoIn, ctx: ToolContext) -> EchoOut:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return EchoOut(text=args.text)


class Private(Echo):
    name = "private"
    requires_verification = True


def ctx(*, verified: bool = False, chaos: ChaosPlan | None = None) -> ToolContext:
    deps = ToolDeps(db=None, retriever=None, settings=Settings(_env_file=None))  # type: ignore[arg-type,call-arg]
    auth = SessionAuth(customer_id="cus_x" if verified else None)
    return ToolContext(deps=deps, session_id="s1", auth=auth, chaos=chaos)


def executor(*tools: Tool[Any, Any], **kw: Any) -> ToolExecutor:
    return ToolExecutor(list(tools), backoff_s=0.001, **kw)


async def test_success() -> None:
    out = await executor(Echo()).execute("echo", {"text": "hi"}, ctx())
    assert out.ok and out.output == {"text": "hi"} and out.attempts == 1
    assert out.for_model() == {"text": "hi"}


async def test_unknown_tool() -> None:
    out = await executor(Echo()).execute("nope", {}, ctx())
    assert not out.ok and out.error and out.error.kind is ErrorKind.UNKNOWN_TOOL


async def test_invalid_arguments_are_reported_not_raised() -> None:
    tool = Echo()
    out = await executor(tool).execute("echo", {"text": "hi", "extra": 1}, ctx())
    assert out.error and out.error.kind is ErrorKind.INVALID_ARGUMENTS
    assert "extra" in out.error.message
    assert tool.calls == 0


async def test_verification_precondition() -> None:
    tool = Private()
    out = await executor(tool).execute("private", {"text": "x"}, ctx(verified=False))
    assert out.error and out.error.kind is ErrorKind.NOT_VERIFIED
    assert tool.calls == 0
    assert (await executor(tool).execute("private", {"text": "x"}, ctx(verified=True))).ok


async def test_retryable_error_is_retried_then_succeeds() -> None:
    tool = Echo(failures=[ToolError(ErrorKind.UPSTREAM, "503")])
    out = await executor(tool).execute("echo", {"text": "x"}, ctx())
    assert out.ok and out.attempts == 2


async def test_non_retryable_error_is_not_retried() -> None:
    tool = Echo(failures=[ToolError(ErrorKind.NOT_FOUND, "missing")])
    out = await executor(tool).execute("echo", {"text": "x"}, ctx())
    assert out.error and out.error.kind is ErrorKind.NOT_FOUND and out.attempts == 1


async def test_exhausted_retries_tell_the_model_to_degrade_gracefully() -> None:
    tool = Echo(failures=[ToolError(ErrorKind.UPSTREAM, "503")] * 3)
    out = await executor(tool).execute("echo", {"text": "x"}, ctx())
    assert out.error and out.error.retryable and out.attempts == 2
    assert "escalation ticket" in out.error.message


async def test_unexpected_exception_becomes_internal_error() -> None:
    tool = Echo(failures=[RuntimeError("boom")])
    out = await executor(tool).execute("echo", {"text": "x"}, ctx())
    assert out.error and out.error.kind is ErrorKind.INTERNAL and out.attempts == 1
    assert "boom" not in out.error.message  # internals never reach the model


async def test_chaos_timeout_once_recovers_on_retry() -> None:
    plan = ChaosPlan.parse("echo:timeout:once", ["echo"])
    out = await executor(Echo(), timeout_override_s=0.05).execute(
        "echo", {"text": "x"}, ctx(chaos=plan)
    )
    assert out.ok and out.attempts == 2


async def test_chaos_always_fails_every_attempt() -> None:
    plan = ChaosPlan.parse("*:error", ["echo"])
    out = await executor(Echo()).execute("echo", {"text": "x"}, ctx(chaos=plan))
    assert out.error and out.error.kind is ErrorKind.UPSTREAM and out.attempts == 2


async def test_chaos_rate_limit_respects_retry_after() -> None:
    plan = ChaosPlan.parse("echo:rate_limit:once", ["echo"])
    out = await executor(Echo()).execute("echo", {"text": "x"}, ctx(chaos=plan))
    assert out.ok and out.attempts == 2


@pytest.mark.parametrize(
    "spec", ["echo", "nope:error", "echo:explode", "echo:error:sometimes", "a:b:c:d"]
)
def test_chaos_plan_rejects_bad_specs(spec: str) -> None:
    with pytest.raises(ChaosPlanError):
        ChaosPlan.parse(spec, ["echo"])


def test_default_tool_specs_are_well_formed() -> None:
    tools = default_tools()
    names = [t.name for t in tools]
    assert names == [
        "verify_identity",
        "get_account_profile",
        "search_policies",
        "issue_refund",
        "update_account_field",
        "create_escalation_ticket",
    ]
    for tool in tools:
        spec = tool.spec()
        assert spec["description"] and tool.label.endswith("...")
        assert spec["parameters"]["type"] == "object"
        assert spec["parameters"].get("additionalProperties") is False
