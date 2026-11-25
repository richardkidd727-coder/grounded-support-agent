"""Index the knowledge base into Postgres.

Usage: uv run ingest [--force]
"""

import argparse
import asyncio

from support_agent.config import get_settings
from support_agent.db import Database
from support_agent.logs import configure_logging
from support_agent.rag.factory import build_chunker_config, build_embedder
from support_agent.rag.ingest import ingest_directory
from support_agent.rag.store import KnowledgeBaseStore


async def _run(force: bool) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    store = KnowledgeBaseStore(db)
    try:
        report = await ingest_directory(
            store,
            build_embedder(settings),
            settings.knowledge_base_dir,
            build_chunker_config(settings),
            force=force,
        )
        stats = await store.stats()
    finally:
        await db.dispose()

    print(
        f"indexed {len(report.indexed)} doc(s) / {report.chunks_written} chunk(s), "
        f"unchanged {len(report.unchanged)}, deleted {len(report.deleted)} "
        f"in {report.duration_s}s -> index now holds {stats.documents} docs, {stats.chunks} chunks"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-embed every document")
    args = parser.parse_args()
    configure_logging(get_settings().log_level)
    asyncio.run(_run(args.force))


if __name__ == "__main__":
    main()
