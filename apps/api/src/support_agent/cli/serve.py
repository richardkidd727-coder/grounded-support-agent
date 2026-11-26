"""Production entrypoint: migrate, index the knowledge base, re-seed the demo, then serve.

    serve            # what the container runs (scripts/start.sh)

All in one interpreter on purpose. Measured at Render's free 0.1 CPU, every Python process pays
10-25s just importing SQLAlchemy/alembic/onnxruntime, so separate `alembic`, `ingest` and `seed`
commands before uvicorn added ~45s to each cold start.

* Migrations and ingest are idempotent (ingest skips unchanged documents), so this runs on every
  boot - Render's free tier has no pre-deploy hook.
* Re-seeding gives every cold start a clean demo with refund windows relative to today.
* No proxy-header rewriting: uvicorn trusting every proxy would take the leftmost, client-forgeable
  `X-Forwarded-For` entry. The app resolves client IPs itself (`TRUSTED_PROXY_HOPS`).
"""

from __future__ import annotations

import asyncio
import os

import uvicorn
from alembic import command
from alembic.config import Config

from support_agent.config import API_ROOT, Settings, get_settings
from support_agent.logs import configure_logging


def prepare(settings: Settings) -> int:
    """Bring the database up to date. Returns the number of demo customers seeded."""
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")

    from support_agent.cli.ingest import _run as ingest
    from support_agent.cli.seed import _run as seed

    asyncio.run(ingest(force=False))
    return asyncio.run(seed())


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.env == "production")
    print(f"boot: seeded {prepare(settings)} demo customers", flush=True)

    from support_agent.main import create_app

    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 - inside a container, behind the host's proxy
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=False,
        timeout_graceful_shutdown=20,  # drain in-flight turns on redeploy
        access_log=False,
    )


if __name__ == "__main__":
    main()
