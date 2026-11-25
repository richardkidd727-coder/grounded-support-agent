"""Reset the mock CRM to the demo personas (dates relative to now).

Usage: uv run seed
"""

import asyncio

from support_agent.config import get_settings
from support_agent.crm.seed import seed_personas
from support_agent.db import Database
from support_agent.logs import configure_logging


async def _run() -> int:
    db = Database(get_settings().database_url)
    try:
        return await seed_personas(db)
    finally:
        await db.dispose()


def main() -> None:
    configure_logging("WARNING")
    count = asyncio.run(_run())
    print(f"seeded {count} demo customers")


if __name__ == "__main__":
    main()
