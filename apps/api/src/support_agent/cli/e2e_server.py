"""Run the API for end-to-end browser tests: isolated database, scripted models, fake voice.

    uv run e2e-server [--port 8100]

* Database: `support_e2e` on the same server as DATABASE_URL (override with E2E_DATABASE_URL),
  created if missing, migrated, knowledge base ingested, demo customers seeded - so the suite
  never touches your dev data.
* Models: deterministic scripted models (no API key, no quota, no randomness).
* Voice: the fake speech backend (pitch-keyed transcripts, tone text-to-speech).
* Chaos enabled, generous rate limits (the whole suite runs from one IP).

Real embeddings (fastembed) are used, so retrieval and the confidence gate behave as in production.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from urllib.parse import urlsplit, urlunsplit

import asyncpg  # type: ignore[import-untyped]
import uvicorn
from alembic import command
from alembic.config import Config

E2E_DB = "support_e2e"

E2E_ENV = {
    "ENV": "test",
    "AGENT_LLM_CHAIN": "fake:scripted-primary,fake:scripted-backup",
    "JUDGE_LLM_CHAIN": "fake:scripted-judge",
    "VOICE_BACKEND": "fake",
    "CHAOS_ENABLED": "true",
    "DEMO_MODE": "true",
    "RATE_LIMIT_PER_MINUTE": "1000",
    "IP_TURNS_PER_MINUTE": "1000",
    "IP_SESSIONS_PER_MINUTE": "1000",
    "VOICE_REQUESTS_PER_MINUTE": "1000",
    "LOG_LEVEL": "WARNING",
}


def _with_db(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


async def _ensure_database(url: str) -> None:
    admin = await asyncpg.connect(_with_db(url, "postgres"), timeout=10)
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", E2E_DB)
        if not exists:
            await admin.execute(f'CREATE DATABASE "{E2E_DB}"')
    finally:
        await admin.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--web-origin", default="http://localhost:3100")
    args = parser.parse_args()

    from support_agent.config import API_ROOT, get_settings  # after env is set below

    base = os.environ.get("E2E_DATABASE_URL") or _with_db(get_settings().database_url, E2E_DB)
    os.environ.update({**E2E_ENV, "DATABASE_URL": base, "CORS_ORIGINS": args.web_origin})
    get_settings.cache_clear()
    settings = get_settings()

    asyncio.run(_ensure_database(settings.database_url))
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")

    from support_agent.cli.ingest import _run as ingest
    from support_agent.cli.seed import _run as seed
    from support_agent.main import create_app

    asyncio.run(ingest(force=False))
    print(f"e2e: seeded {asyncio.run(seed())} customers; serving on :{args.port}", flush=True)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
