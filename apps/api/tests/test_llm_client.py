"""OpenAI-compatible streaming client: SSE parsing, error mapping, retries, rate-limit budget."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from support_agent.llm.openai_compat import OpenAICompatClient
from support_agent.llm.schema import compact_schema
from support_agent.llm.types import (
    ChatMessage,
    LlmError,
    LlmErrorKind,
    LlmRequest,
    LlmResult,
    TextDelta,
)

REQUEST = LlmRequest(messages=[ChatMessage("user", "hi")])


def sse(*chunks: dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def delta(**d: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": d, "finish_reason": None}]}


def client_for(
    handler: Callable[[httpx.Request], httpx.Response], *, model: str = "openai/gpt-oss-120b"
) -> OpenAICompatClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatClient("groq", model, "test-key", http=http)


async def collect(client: OpenAICompatClient, request: LlmRequest = REQUEST) -> list[Any]:
    return [item async for item in client.stream(request)]


async def test_streams_text_then_a_result_with_usage() -> None:
    body = sse(
        delta(role="assistant", content="Hel"),
        delta(content="lo"),
        delta(reasoning="internal thoughts"),  # ignored
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 4},
            },
        },
    )
    items = await collect(client_for(lambda _: httpx.Response(200, content=body)))
    assert [i.text for i in items if isinstance(i, TextDelta)] == ["Hel", "lo"]
    result = items[-1]
    assert isinstance(result, LlmResult)
    assert (result.text, result.finish_reason) == ("Hello", "stop")
    assert (result.usage.input_tokens, result.usage.output_tokens) == (12, 3)
    assert result.usage.cached_input_tokens == 4
    assert result.ttft_ms is not None


async def test_assembles_tool_calls_split_across_chunks() -> None:
    body = sse(
        delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "verify_identity"}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": '{"email":"a@b.c",'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": '"card_last4":"4242"}'}}]),
        delta(tool_calls=[{"index": 1, "id": "c2", "function": {"name": "x", "arguments": "{}"}}]),
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "x_groq": {"usage": {"prompt_tokens": 5, "completion_tokens": 9}}},
    )
    items = await collect(client_for(lambda _: httpx.Response(200, content=body)))
    result = items[-1]
    assert isinstance(result, LlmResult)
    assert [c.id for c in result.tool_calls] == ["c1", "c2"]
    assert result.tool_calls[0].arguments == {"email": "a@b.c", "card_last4": "4242"}
    assert result.usage.output_tokens == 9  # Groq's x_groq usage location


async def test_request_body_compacts_tools_and_sets_model_options() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, content=sse(delta(content="ok")))

    tool = {
        "name": "t",
        "description": "d",
        "parameters": {
            "title": "TInput",
            "type": "object",
            "properties": {"kind": {"$ref": "#/$defs/Kind", "title": "Kind"}},
            "$defs": {"Kind": {"enum": ["a", "b"], "title": "Kind", "type": "string"}},
        },
    }
    await collect(client_for(handler), LlmRequest(messages=REQUEST.messages, tools=[tool]))
    params = seen["tools"][0]["function"]["parameters"]
    assert params == {
        "type": "object",
        "properties": {"kind": {"enum": ["a", "b"], "type": "string"}},
    }
    assert seen["tool_choice"] == "auto"
    assert seen["reasoning_effort"] == "low"  # gpt-oss tweak
    assert seen["stream"] is True


def test_compact_schema_keeps_a_property_named_title() -> None:
    schema = {"type": "object", "title": "X", "properties": {"title": {"type": "string"}}}
    assert compact_schema(schema) == {"type": "object", "properties": {"title": {"type": "string"}}}


@pytest.mark.parametrize(
    ("status", "body", "headers", "kind", "retry_after"),
    [
        (
            429,
            {"error": {"message": "Rate limit reached"}},
            {"retry-after": "7"},
            "rate_limited",
            7,
        ),
        (
            429,
            {"error": {"message": "Please try again in 1m2.5s."}},
            {},
            "rate_limited",
            62.5,
        ),
        (413, {"error": {"message": "Request too large"}}, {}, "rate_limited", None),
        (
            403,
            {"error": {"message": "The Gemini API free tier is not available for this project."}},
            {},
            "quota",
            None,
        ),
        (401, {"error": {"message": "Invalid API Key"}}, {}, "auth", None),
        (
            400,
            {"error": {"message": "Failed to call a function.", "code": "tool_use_failed"}},
            {},
            "bad_output",
            None,
        ),
        (400, {"error": {"message": "messages: required"}}, {}, "bad_request", None),
        (503, {"error": {"message": "over capacity"}}, {}, "unavailable", None),
    ],
)
async def test_http_errors_are_classified(
    status: int, body: dict[str, Any], headers: dict[str, str], kind: str, retry_after: float | None
) -> None:
    client = client_for(lambda _: httpx.Response(status, json=body, headers=headers))
    with pytest.raises(LlmError) as info:
        await collect(client)
    assert info.value.kind == LlmErrorKind(kind)
    assert info.value.retry_after_s == retry_after
    assert "test-key" not in str(info.value)


async def test_connect_failures_are_retried_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("tls handshake failed", request=request)
        return httpx.Response(200, content=sse(delta(content="ok")))

    items = await collect(client_for(handler))
    assert calls == 2
    assert isinstance(items[-1], LlmResult) and items[-1].text == "ok"


async def test_read_timeouts_are_not_retried_and_mark_partial_output() -> None:
    class Flaky(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            yield f"data: {json.dumps(delta(content='Hel'))}\n\n".encode()
            raise httpx.ReadTimeout("stalled")

    client = client_for(lambda _: httpx.Response(200, stream=Flaky()))
    seen: list[Any] = []
    with pytest.raises(LlmError) as info:
        async for item in client.stream(REQUEST):
            seen.append(item)
    assert info.value.kind is LlmErrorKind.TIMEOUT
    assert info.value.partial  # the user already saw "Hel": the router must not fall back
    assert [i.text for i in seen] == ["Hel"]


async def test_tracks_the_per_minute_token_budget_from_headers() -> None:
    headers = {"x-ratelimit-limit-tokens": "6000", "x-ratelimit-remaining-tokens": "1000"}
    body = sse(delta(content="ok"), {"choices": [], "usage": {"completion_tokens": 400}})
    client = client_for(lambda _: httpx.Response(200, content=body, headers=headers))
    assert client.budget_wait_s(5000) == 0.0  # unknown until the first response
    await collect(client)
    # 1000 left minus 400 completion tokens = 600; refill is 100 tokens/s.
    assert client.budget_wait_s(500) == 0.0
    assert client.budget_wait_s(1600) == pytest.approx(10.0, abs=0.2)
    assert client.budget_wait_s(7000) == 0.0  # can never fit: let the provider say so
