# support-agent (API)

FastAPI backend: agent orchestrator, tools, RAG and session/trace stores.

```bash
uv sync                      # install
uv run uvicorn support_agent.main:app --reload --port 8000
uv run pytest                # tests
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

See the root `README.md` for the full architecture.
