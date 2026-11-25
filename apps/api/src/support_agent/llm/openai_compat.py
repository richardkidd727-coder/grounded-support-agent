"""Streaming client for OpenAI-compatible chat completion APIs.

Groq and Gemini (`/v1beta/openai`) both speak this dialect, so one well-tested client covers
every model in the fallback chain; switching provider is configuration, not code.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from support_agent.llm.schema import compact_schema
from support_agent.llm.types import (
    LlmError,
    LlmErrorKind,
    LlmRequest,
    LlmResult,
    ProviderName,
    StreamItem,
    TextDelta,
    ToolCall,
    Usage,
)

BASE_URLS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}

_RETRY_IN = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.IGNORECASE)


def model_options(provider: str, model: str) -> dict[str, Any]:
    """Per-model request tweaks."""
    if "gpt-oss" in model:
        # Reasoning tokens count against the per-minute budget; tool routing needs little.
        return {"reasoning_effort": "low"}
    return {}


@dataclass(slots=True)
class _PartialCall:
    id: str = ""
    name: str = ""
    args: list[str] = field(default_factory=list)


class OpenAICompatClient:
    def __init__(
        self,
        provider: ProviderName,
        model: str,
        api_key: str,
        *,
        http: httpx.AsyncClient,
        base_url: str | None = None,
        read_timeout_s: float = 20.0,
        total_timeout_s: float = 45.0,
    ) -> None:
        self.provider = provider
        self.model = model
        self._api_key = api_key
        self._http = http
        self._url = f"{(base_url or BASE_URLS[provider]).rstrip('/')}/chat/completions"
        self._timeout = httpx.Timeout(connect=10.0, read=read_timeout_s, write=10.0, pool=10.0)
        self._total_timeout_s = total_timeout_s
        # (tokens-per-minute limit, remaining, when observed) from rate-limit response headers
        self._budget: tuple[int, float, float] | None = None

    def budget_wait_s(self, tokens: int) -> float:
        """Seconds until the per-minute token bucket can hold `tokens` (0 if unknown/fine).

        Providers like Groq report the bucket in response headers; it refills linearly over a
        minute. Used by the router to skip a model *before* a request that would be a 429.
        """
        if self._budget is None:
            return 0.0
        limit, remaining, seen = self._budget
        if tokens >= limit:  # can never fit; let the provider answer (413) and fall back
            return 0.0
        rate = limit / 60.0
        available = min(float(limit), remaining + (time.monotonic() - seen) * rate)
        return 0.0 if available >= tokens else (tokens - available) / rate

    def _record_budget(self, headers: httpx.Headers) -> None:
        try:
            limit = int(headers["x-ratelimit-limit-tokens"])
            remaining = float(headers["x-ratelimit-remaining-tokens"])
        except (KeyError, ValueError):
            return
        self._budget = (limit, remaining, time.monotonic())

    def _spend(self, tokens: int) -> None:
        if self._budget is not None:
            limit, remaining, seen = self._budget
            self._budget = (limit, remaining - tokens, seen)

    def __repr__(self) -> str:  # never print the key
        return f"OpenAICompatClient({self.provider}:{self.model})"

    def _body(self, request: LlmRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_openai() for m in request.messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": request.temperature,
            ("max_completion_tokens" if self.provider == "groq" else "max_tokens"): (
                request.max_output_tokens
            ),
            **model_options(self.provider, self.model),
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": compact_schema(t["parameters"]),
                    },
                }
                for t in request.tools
            ]
            body["tool_choice"] = "auto"
        if request.json_output:
            body["response_format"] = {"type": "json_object"}
        return body

    def _error(self, kind: LlmErrorKind, message: str, **kw: Any) -> LlmError:
        return LlmError(kind, message, provider=self.provider, model=self.model, **kw)

    async def stream(self, request: LlmRequest) -> AsyncIterator[StreamItem]:
        try:
            async for item in self._stream_once(request):
                yield item
        except LlmError as exc:
            if not exc.connect_failed:
                raise
            # The connection was never established, so nothing was sent: retrying is always
            # safe, and a fresh TLS handshake usually gets through a transient network blip.
            async for item in self._stream_once(request):
                yield item

    async def _stream_once(self, request: LlmRequest) -> AsyncIterator[StreamItem]:
        started = time.perf_counter()
        streamed_text = False
        try:
            async with self._http.stream(
                "POST",
                self._url,
                json=self._body(request),
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            ) as response:
                self._record_budget(response.headers)
                if response.status_code != 200:
                    raise self._http_error(response, await response.aread())
                async for item in self._parse(response, started):
                    if isinstance(item, TextDelta):
                        streamed_text = True
                    yield item
        except LlmError as exc:
            exc.partial = exc.partial or streamed_text
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            error = self._error(
                LlmErrorKind.UNAVAILABLE, f"could not connect ({type(exc).__name__})"
            )
            error.connect_failed = True
            raise error from exc
        except httpx.TimeoutException as exc:
            raise self._error(
                LlmErrorKind.TIMEOUT,
                f"no response within the deadline ({type(exc).__name__})",
                partial=streamed_text,
            ) from exc
        except httpx.TransportError as exc:
            raise self._error(
                LlmErrorKind.UNAVAILABLE,
                f"connection failed ({type(exc).__name__})",
                partial=streamed_text,
            ) from exc

    async def _parse(self, response: httpx.Response, started: float) -> AsyncIterator[StreamItem]:
        text: list[str] = []
        calls: dict[int, _PartialCall] = {}
        usage = Usage()
        finish: str | None = None
        ttft_ms: int | None = None

        async for line in response.aiter_lines():
            if time.perf_counter() - started > self._total_timeout_s:
                raise self._error(LlmErrorKind.TIMEOUT, "generation exceeded the total deadline")
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if "error" in chunk:  # some providers report mid-stream failures in-band
                raise self._error(LlmErrorKind.UNAVAILABLE, _message(chunk)[:300])

            raw_usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
            if raw_usage:
                usage = Usage(
                    input_tokens=int(raw_usage.get("prompt_tokens") or 0),
                    output_tokens=int(raw_usage.get("completion_tokens") or 0),
                    cached_input_tokens=int(
                        (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                    ),
                )
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    if ttft_ms is None:
                        ttft_ms = round((time.perf_counter() - started) * 1000)
                    text.append(content)
                    yield TextDelta(content)
                for raw in delta.get("tool_calls") or []:
                    index = raw.get("index")
                    if index is None:  # some providers send whole calls without an index
                        index = len(calls)
                    part = calls.setdefault(index, _PartialCall())
                    part.id = raw.get("id") or part.id
                    fn = raw.get("function") or {}
                    part.name = fn.get("name") or part.name
                    if fn.get("arguments"):
                        part.args.append(fn["arguments"])
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]

        tool_calls = tuple(
            ToolCall(id=p.id or f"call_{i}", name=p.name, raw_arguments="".join(p.args))
            for i, p in sorted(calls.items())
            if p.name
        )
        self._spend(usage.output_tokens)  # headers counted the prompt, not the completion
        yield LlmResult(
            provider=self.provider,
            model=self.model,
            text="".join(text),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish,
            latency_ms=round((time.perf_counter() - started) * 1000),
            ttft_ms=ttft_ms,
        )

    def _http_error(self, response: httpx.Response, raw: bytes) -> LlmError:
        status = response.status_code
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        message = (_message(payload) or raw.decode(errors="replace"))[:300]
        lowered = message.lower()

        if status == 429:
            return self._error(
                LlmErrorKind.RATE_LIMITED, message, retry_after_s=_retry_after(response, message)
            )
        if status == 413 or "request too large" in lowered:
            # A single request above the per-minute token budget: another model may fit it.
            return self._error(LlmErrorKind.RATE_LIMITED, message)
        if status in (401, 403):
            if "free tier" in lowered or "billing" in lowered or "quota" in lowered:
                return self._error(LlmErrorKind.QUOTA, message)
            return self._error(LlmErrorKind.AUTH, message)
        if status == 400 and (
            "tool_use_failed" in raw.decode(errors="replace")
            or "failed to call a function" in lowered
            or "tool call validation failed" in lowered
        ):
            # The model produced a malformed tool call; the next model in the chain usually won't.
            return self._error(LlmErrorKind.BAD_OUTPUT, message)
        if status >= 500:
            return self._error(LlmErrorKind.UNAVAILABLE, f"HTTP {status}: {message}")
        return self._error(LlmErrorKind.BAD_REQUEST, f"HTTP {status}: {message}")


def _message(payload: Any) -> str:
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)
        if isinstance(err, str):
            return err
    return ""


def _retry_after(response: httpx.Response, message: str) -> float | None:
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = _RETRY_IN.search(message)
    if match:
        return int(match.group(1) or 0) * 60 + float(match.group(2))
    return None
