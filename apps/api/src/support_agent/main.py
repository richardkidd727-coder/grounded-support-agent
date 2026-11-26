"""FastAPI application factory."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import DBAPIError

from support_agent import __version__
from support_agent.agent.grounding import GroundingJudge
from support_agent.agent.orchestrator import Agent
from support_agent.agent.runner import TurnRunner
from support_agent.agent.store import SessionStore
from support_agent.api import chat, demo, health, kb, observability, voice
from support_agent.api.middleware import (
    REQUEST_ID_HEADER,
    database_unavailable,
    make_request_context,
    unhandled_error,
)
from support_agent.config import Settings, get_settings
from support_agent.db import Database
from support_agent.llm.factory import build_router
from support_agent.llm.router import LlmRouter
from support_agent.logs import configure_logging, get_logger
from support_agent.protection import Limits
from support_agent.rag.embeddings import Embedder, FastEmbedEmbedder
from support_agent.rag.factory import build_embedder, build_retriever
from support_agent.tools.base import ToolDeps
from support_agent.tools.registry import build_executor
from support_agent.voice.backend import SpeechBackend
from support_agent.voice.fake_audio import FakeAudio
from support_agent.voice.groq_audio import GroqAudio

log = get_logger("app")


def build_speech_backend(settings: Settings, http: httpx.AsyncClient) -> SpeechBackend:
    if settings.voice_backend == "fake":
        return FakeAudio([p.strip() for p in settings.fake_stt_phrases.split("|") if p.strip()])
    key = settings.groq_api_key.get_secret_value() if settings.groq_api_key else None
    return GroqAudio(
        http,
        key or None,
        stt_model=settings.groq_stt_model,
        tts_model=settings.groq_tts_model,
        tts_voice=settings.groq_tts_voice,
    )


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    embedder: Embedder | None = None,
    agent_llm: LlmRouter | None = None,
    judge_llm: LlmRouter | None = None,
    audio: SpeechBackend | None = None,
) -> FastAPI:
    """Build the app. Dependencies can be injected (tests); otherwise built from settings."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.env == "production")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.db = db or Database(settings.database_url)
        app.state.embedder = embedder or build_embedder(settings)
        app.state.retriever = build_retriever(settings, app.state.db, app.state.embedder)
        app.state.tool_deps = ToolDeps(app.state.db, app.state.retriever, settings)
        app.state.tools = build_executor()
        app.state.limits = Limits(settings)

        http = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10)
        )
        app.state.agent_llm = agent_llm or build_router(settings.agent_llm_chain, settings, http)
        judge = judge_llm or build_router(settings.judge_llm_chain, settings, http)
        app.state.runner = TurnRunner(
            Agent(
                llm=app.state.agent_llm,
                judge=GroundingJudge(judge),
                tools=app.state.tools,
                deps=app.state.tool_deps,
                settings=settings,
            ),
            SessionStore(app.state.db, ttl_hours=settings.session_ttl_hours),
        )
        app.state.audio = audio or build_speech_backend(settings, http)
        if not app.state.agent_llm.configured:
            log.warning("no_llm_configured", hint="set GROQ_API_KEY and/or GEMINI_API_KEY")

        warm_up: asyncio.Task[None] | None = None
        if isinstance(app.state.embedder, FastEmbedEmbedder):
            # Load the ONNX model off the request path so the first question is not slow.
            warm_up = asyncio.create_task(asyncio.to_thread(app.state.embedder.warm_up))
        log.info(
            "startup", env=settings.env, version=__version__, llm_chain=app.state.agent_llm.chain
        )
        try:
            yield
        finally:
            await app.state.runner.shutdown()
            if warm_up and not warm_up.done():
                warm_up.cancel()
            await http.aclose()
            await app.state.db.dispose()
            log.info("shutdown")

    app = FastAPI(title="Support Agent API", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        # explicit, not "*": exactly what the web client sends
        allow_headers=["Content-Type", "Accept", "X-Chaos", REQUEST_ID_HEADER, "Authorization"],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
    )
    app.middleware("http")(make_request_context(hsts=settings.env == "production"))
    # Postgres unreachable -> retryable 503 (TimeoutError/ConnectionError are OSErrors);
    # anything unexpected -> 500 with a request id, never a stack trace.
    app.add_exception_handler(DBAPIError, database_unavailable)
    app.add_exception_handler(OSError, database_unavailable)
    app.add_exception_handler(Exception, unhandled_error)
    app.include_router(health.router)
    app.include_router(kb.router)
    app.include_router(chat.router)
    app.include_router(voice.router)
    app.include_router(observability.router)
    if settings.demo_mode:
        app.include_router(demo.router)
    return app


app = create_app()
