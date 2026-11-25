"""Liveness / readiness probes.

`/healthz` never touches dependencies (used by the frontend's wake-up ping and the host's
liveness check). `/readyz` verifies the database so a deploy is only marked ready when usable,
and reports whether any LLM is configured (status "degraded" without one, but still 200: the
API can serve everything except agent turns).
"""

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from support_agent import __version__
from support_agent.db import Database
from support_agent.llm.router import LlmRouter

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: Literal["ok"]
    version: str


class Readiness(BaseModel):
    status: Literal["ready", "degraded"]
    database: bool
    llm_models: list[str]


@router.get("/healthz")
async def healthz() -> Health:
    return Health(status="ok", version=__version__)


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> Readiness:
    db: Database = request.app.state.db
    db_ok = await db.ping()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    llm: LlmRouter = request.app.state.agent_llm
    return Readiness(
        status="ready" if db_ok and llm.configured else "degraded",
        database=db_ok,
        llm_models=llm.chain,
    )
