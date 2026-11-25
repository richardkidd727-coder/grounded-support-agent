FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev

RUN useradd --create-home app
USER app

ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8000
EXPOSE 8000
# Render injects $PORT; fall back to 8000 locally. Graceful shutdown drains in-flight streams.
CMD ["sh", "-c", "uvicorn support_agent.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 20 --no-access-log"]
