"""Request-scoped middleware: correlation ids and access logging."""

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response

from support_agent.logs import get_logger

REQUEST_ID_HEADER = "X-Request-ID"
log = get_logger("http")


async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)

    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    response.headers[REQUEST_ID_HEADER] = request_id
    if request.url.path != "/healthz":  # keep wake-up pings out of the logs
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=elapsed_ms,
        )
    return response
