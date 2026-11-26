"""Service-wide metrics for the observability drawer (aggregates only, no customer data).

Per-conversation traces are served with the session (`GET /api/sessions/{id}/turns`); this is
the operator view across all sessions. Open in the demo; set `OBSERVABILITY_TOKEN` to require
`Authorization: Bearer <token>`.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from support_agent.config import Settings
from support_agent.observability import ServiceSummary, service_summary
from support_agent.tools.base import utcnow

router = APIRouter(prefix="/api/observability", tags=["observability"])


def _authorize(settings: Settings, authorization: str | None) -> None:
    token = settings.observability_token
    if token is None:
        return
    expected = f"Bearer {token.get_secret_value()}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "observability token required",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/summary")
async def summary(
    request: Request,
    window_hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    authorization: Annotated[str | None, Header()] = None,
) -> ServiceSummary:
    settings: Settings = request.app.state.settings
    _authorize(settings, authorization)
    return await service_summary(request.app.state.db, now=utcnow(), window_hours=window_hours)
