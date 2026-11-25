"""Liveness / readiness probes.

`/healthz` never touches dependencies (used by the frontend's wake-up ping and the host's
liveness check). `/readyz` verifies the database so a deploy is only marked ready when usable.
"""

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from support_agent import __version__
from support_agent.db import Database

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: Literal["ok"]
    version: str


class Readiness(BaseModel):
    status: Literal["ready", "degraded"]
    database: bool


@router.get("/healthz")
async def healthz() -> Health:
    return Health(status="ok", version=__version__)


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> Readiness:
    db: Database = request.app.state.db
    db_ok = await db.ping()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ready" if db_ok else "degraded", database=db_ok)
