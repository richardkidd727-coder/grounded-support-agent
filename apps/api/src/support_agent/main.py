"""FastAPI application factory."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from support_agent import __version__
from support_agent.api import health, kb
from support_agent.api.middleware import REQUEST_ID_HEADER, request_context
from support_agent.config import Settings, get_settings
from support_agent.db import Database
from support_agent.logs import configure_logging, get_logger
from support_agent.rag.embeddings import Embedder, FastEmbedEmbedder
from support_agent.rag.factory import build_embedder, build_retriever

log = get_logger("app")


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    embedder: Embedder | None = None,
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

        warm_up: asyncio.Task[None] | None = None
        if isinstance(app.state.embedder, FastEmbedEmbedder):
            # Load the ONNX model off the request path so the first question is not slow.
            warm_up = asyncio.create_task(asyncio.to_thread(app.state.embedder.warm_up))
        log.info("startup", env=settings.env, version=__version__)
        try:
            yield
        finally:
            if warm_up and not warm_up.done():
                warm_up.cancel()
            await app.state.db.dispose()
            log.info("shutdown")

    app = FastAPI(title="Support Agent API", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
    )
    app.middleware("http")(request_context)
    app.include_router(health.router)
    app.include_router(kb.router)
    return app


app = create_app()
