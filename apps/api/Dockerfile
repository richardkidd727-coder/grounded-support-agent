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
ENV PATH="/app/.venv/bin:$PATH"

# Bake the embedding model into the image: cold starts never download it.
RUN python -c "from fastembed import TextEmbedding; \
TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/.cache/fastembed')"

COPY alembic.ini ./
COPY migrations ./migrations
COPY knowledge_base ./knowledge_base
COPY scripts/start.sh ./start.sh

RUN useradd --create-home app && chmod +x start.sh && chown -R app /app/.cache
USER app

ENV PORT=8000
EXPOSE 8000
CMD ["./start.sh"]
