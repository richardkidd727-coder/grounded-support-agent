"""Build model routers from settings (`AGENT_LLM_CHAIN`, `JUDGE_LLM_CHAIN`)."""

from __future__ import annotations

from typing import cast

import httpx

from support_agent.config import Settings
from support_agent.llm.openai_compat import BASE_URLS, OpenAICompatClient
from support_agent.llm.router import LlmRouter
from support_agent.llm.scripted import ScriptedSupportModel
from support_agent.llm.types import LlmClient, ProviderName
from support_agent.logs import get_logger

log = get_logger("llm")


def parse_chain(spec: str) -> list[tuple[ProviderName, str]]:
    """`"groq:openai/gpt-oss-120b, gemini:gemini-2.5-flash"` -> [(provider, model), ...]"""
    entries: list[tuple[ProviderName, str]] = []
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        provider, sep, model = item.partition(":")
        if not sep or not model or provider not in {*BASE_URLS, "fake"}:
            raise ValueError(
                f"bad LLM chain entry {item!r}; expected one of {sorted({*BASE_URLS, 'fake'})}"
            )
        entries.append((cast(ProviderName, provider), model))
    return entries


def _api_key(settings: Settings, provider: str) -> str | None:
    secret = settings.groq_api_key if provider == "groq" else settings.gemini_api_key
    value = secret.get_secret_value().strip() if secret else ""
    return value or None


def build_router(spec: str, settings: Settings, http: httpx.AsyncClient) -> LlmRouter:
    clients: list[LlmClient] = []
    for provider, model in parse_chain(spec):
        if provider == "fake":  # deterministic scripted model (E2E tests, offline demos)
            clients.append(ScriptedSupportModel(model))
            continue
        key = _api_key(settings, provider)
        if key is None:
            log.warning("llm_skipped_no_key", provider=provider, model=model)
            continue
        clients.append(
            OpenAICompatClient(
                provider,
                model,
                key,
                http=http,
                read_timeout_s=settings.llm_read_timeout_s,
                total_timeout_s=settings.llm_total_timeout_s,
            )
        )
    return LlmRouter(clients, max_cooldown_wait_s=settings.llm_max_cooldown_wait_s)
