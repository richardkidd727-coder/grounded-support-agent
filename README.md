# Customer Support AI Agent

A full-stack support agent with streaming chat, live voice, multi-step tool orchestration,
grounded RAG with confidence guardrails, and a real-time observability drawer.

> Status: **Phase 0 - foundations.** The full architecture write-up, chunking rationale and
> trade-offs land here in Phase 8.

## Repository layout

```
apps/
  api/              FastAPI backend - orchestrator, tools, RAG, session + trace stores (Python 3.12+)
  web/              Next.js frontend - chat, voice, observability drawer
packages/
  shared-types/     TS types generated from the API's Pydantic event schema
infra/db/           Postgres init (pgvector)
docker-compose.yml  Local Postgres + pgvector (and an optional prod-like API container)
```

**One event protocol.** `apps/api/src/support_agent/events/schema.py` defines every event a turn
can emit (step started/completed, tokens, guardrail verdicts, escalations, metrics, errors). Chat,
voice and the observability drawer all consume it. TypeScript types are generated from it with
`npm run gen:types`, and CI fails if they drift.

## Free-tier stack

| Concern | Choice |
|---|---|
| Agent LLM | Gemini (Google AI Studio free tier), Groq as rate-limit fallback |
| Voice | Gemini Live API (native audio, browser connects with an ephemeral token) |
| Embeddings | `fastembed` running locally (`bge-small-en-v1.5`), no key |
| Database / vectors | Postgres + pgvector (Docker locally, Neon when deployed) |
| Hosting | Vercel (web) + Render (API) |

## Local development

Prerequisites: Docker, Node 24+, [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env          # no keys needed until Phase 3
npm install                   # web + shared-types workspaces
(cd apps/api && uv sync)      # Python deps

npm run db:up                 # Postgres + pgvector on localhost:55432
npm run dev:api               # http://localhost:8000  (/healthz, /readyz, /docs)
npm run dev:web               # http://localhost:3000
```

Checks (same as CI):

```bash
cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
npm run lint && npm run typecheck && npm run build:web
npm run gen:types && git diff --exit-code packages/shared-types
```
