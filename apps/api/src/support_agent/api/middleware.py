"""Request-scoped middleware: correlation ids, security headers, access logging, error shaping."""

import re
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

from support_agent.logs import get_logger

REQUEST_ID_HEADER = "X-Request-ID"
log = get_logger("http")

# Accept a caller's correlation id only if it is short and boring: it ends up in every log line.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")  # Swagger UI needs scripts; skip strict CSP

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}
API_CSP = "default-src 'none'; frame-ancestors 'none'"  # JSON/SSE/WAV only: nothing to execute


def security_headers(path: str, *, hsts: bool) -> dict[str, str]:
    headers = dict(SECURITY_HEADERS)
    if not path.startswith(_DOCS_PATHS):
        headers["Content-Security-Policy"] = API_CSP
    if path.startswith("/api/"):
        headers["Cache-Control"] = "no-store"  # conversations, identities, traces
    if hsts:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


def make_request_context(
    *, hsts: bool
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

        response.headers[REQUEST_ID_HEADER] = request_id
        for name, value in security_headers(request.url.path, hsts=hsts).items():
            response.headers.setdefault(name, value)
        if request.url.path != "/healthz":  # keep wake-up pings out of the logs
            log.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                ms=elapsed_ms,
            )
        return response

    return request_context


def _request_id() -> str | None:
    value = structlog.contextvars.get_contextvars().get("request_id")
    return str(value) if value else None


async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    """Postgres unreachable (cold start, failover, outage): a retryable 503, not a 500."""
    log.warning("database_unavailable", path=request.url.path, error=type(exc).__name__)
    return JSONResponse(
        {"detail": "The service is temporarily unavailable. Please retry shortly."},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers={"Retry-After": "5", **SECURITY_HEADERS},
    )


async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: log the stack trace, return an id to quote - never internals to the client."""
    log.exception("unhandled_error", path=request.url.path)
    return JSONResponse(
        {"detail": "Internal error.", "request_id": _request_id()},
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        headers=SECURITY_HEADERS,
    )
