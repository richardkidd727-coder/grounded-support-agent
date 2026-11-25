# Customer Support AI Agent

A full-stack support agent with streaming chat, live voice, multi-step tool orchestration,
grounded RAG with confidence guardrails, and a real-time observability drawer.

> Status: **Phase 2 - mock CRM & tools.** The full architecture write-up and trade-offs land
> here in Phase 8.

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

## RAG & grounding

The corpus is 15 support policies for **Driftwood**, a fictional file-sync SaaS
(`apps/api/knowledge_base/`). Sections that only apply to some plans are tagged
`<!-- audience: pro -->`.

**Chunking - structure-aware, not fixed-size.** Policy text is rules: a sentence plus the list or
table that qualifies it. Fixed windows cut them apart, which produces confident wrong answers. The
chunker (`rag/chunking.py`) splits on headings first (a chunk never spans two sections), keeps
lists, tables and code atomic, glues a lead-in sentence ending in ":" to the list it introduces,
and only splits an oversize block along its own seams (list items, table rows with the header
repeated, sentences). Each chunk is embedded with its breadcrumb
(`Refund Policy > Pro plan > Monthly Pro subscriptions`), so short sections keep their context.
Small boundary blocks are carried into the next chunk as overlap.

**Retrieval - hybrid with a hard tier filter.** pgvector cosine (HNSW) and Postgres full-text
search (headings weighted above body) run in one SQL round trip and are fused with Reciprocal Rank
Fusion. The audience filter lives in the SQL `WHERE`, so another tier's policy is never even a
candidate - a Free customer cannot be quoted Enterprise refund limits. Unverified callers only see
content marked `all`.

**Confidence gate - calibrated, three bands.** `npm run kb:eval` runs 42 labelled queries (30
answerable, 12 out of scope, including near-misses like "refund my gym membership") and writes
[`eval/results/retrieval.md`](apps/api/eval/results/retrieval.md):

| Metric | Result |
|---|---|
| Hit@1 / Hit@3 / MRR | 90% / 100% / 0.950 |
| Answerable top similarity | 0.583 - 0.878 |
| Out-of-scope top similarity | 0.403 - 0.698 |

The ranges overlap, so no single similarity threshold is safe. The gate therefore has three bands:
below **0.58** reject without calling an LLM (no answerable query scored lower), at or above
**0.70** proceed (no out-of-scope query scored higher), and in between hand the decision to the
grounding judge (Phase 3). The bge query instruction was measured, not assumed: it halves the share
of answerable questions that need the judge (23% vs 47%).

## Tools & policy enforcement

The agent has six tools (`apps/api/src/support_agent/tools/`):

| Tool | Needs verified identity | What it does |
|---|---|---|
| `verify_identity` | - | Email + last 4 card digits *or* a recent invoice number; locks after 3 failures |
| `get_account_profile` | yes | Tier, account status, subscription, Enterprise contract, recent invoices |
| `search_policies` | - | Hybrid RAG, filtered to the verified customer's tier, with a confidence band |
| `issue_refund` | yes | Returns `approved` / `denied` / `requires_approval` |
| `update_account_field` | yes | Allow-listed fields only (never login email, password, 2FA), audited |
| `create_escalation_ticket` | - | Structured hand-off with priority and the plan's response target |

**The model proposes; code decides.** `crm/policy.py::evaluate_refund` is a pure function that
applies every refund rule (60-day cap, plan windows, $100 / $500 agent limits, prorating, goodwill
once a year, contract status, suspension, non-refundable charge types). The refund tool loads the
data, row-locks the invoice, asks the policy engine and writes in one transaction; a database
`CHECK` makes over-refunding impossible even if code were wrong. A test asserts the policy
documents state the same limits the code enforces.

**Identity comes from the session, never from model arguments.** Which customer is verified, their
tier and the session id live in server-side `ToolContext`. The model cannot claim to be another
customer, widen retrieval to another tier, or name a database column.

**Failures are values, not exceptions.** `ToolExecutor` validates arguments against each tool's
Pydantic schema, checks preconditions, applies timeouts, retries timeouts / 5xx / 429 with backoff
(safe because refunds and tickets use idempotency keys), and always returns a structured
`ToolOutcome` telling the model what to do next. `CHAOS_ENABLED=true` plus a plan such as
`issue_refund:timeout:once` injects faults for the demo.

**Demo personas** (`crm/personas.py`, also served at `/api/demo/personas`) each exercise a branch:
an approved refund, an annual refund needing approval, Enterprise credits, an expired contract,
a Free accidental upgrade, the goodwill limit, a suspended account and an out-of-window charge.
Dates are relative to seed time and the container re-seeds on boot, so the scenarios never go stale.

## Local development

Prerequisites: Docker, Node 24+, [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env          # no keys needed until Phase 3
npm install                   # web + shared-types workspaces
(cd apps/api && uv sync)      # Python deps

npm run db:up                 # Postgres + pgvector on localhost:55432
npm run db:migrate            # create tables + indexes
npm run db:seed               # load the demo customers (re-run any time to reset)
npm run kb:ingest             # chunk + embed + index (first run downloads the model, ~130 MB)
npm run kb:eval               # optional: retrieval metrics + gate calibration
npm run dev:api               # http://localhost:8000  (/healthz, /readyz, /docs, /api/kb/search)
npm run dev:web               # http://localhost:3000
```

Checks (same as CI):

```bash
cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
npm run lint && npm run typecheck && npm run build:web
npm run gen:types && git diff --exit-code packages/shared-types
```
