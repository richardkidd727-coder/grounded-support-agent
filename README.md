# Customer Support AI Agent

A full-stack support agent with streaming chat, live voice, multi-step tool orchestration,
grounded RAG with confidence guardrails, and a real-time observability drawer.

> Status: **Phase 4 - streaming chat UI.** The full architecture write-up and trade-offs land
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
| Agent LLM | Groq free tier: `gpt-oss-120b`, falling back to `qwen3.8-27b` (Gemini plugs in by config) |
| Grounding judge | Groq `gpt-oss-20b` (JSON verdicts only) |
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
grounding judge (see [Agent loop](#agent-loop)). The bge query instruction was measured, not assumed: it halves the share
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

## Agent loop

```
customer message ──> model ──tool calls──> executor (validate · identity check · timeout · retry)
                      ^                       │  search_policies ──> confidence gate (+ judge)
                      └──── tool results ─────┘  create_escalation_ticket ──> `escalated` event
                 model answers without tools ──> tokens stream to the client
```

`agent/orchestrator.py` runs one turn as a loop of model calls and tool calls (at most 8 model
calls) and emits every step on the event protocol: `step.started` / `step.completed` for each
model call, tool and judge, `token` deltas, `guardrail.result`, `escalated`, `metrics` and
exactly one `turn.completed`.

**Grounding is enforced in code, not requested in the prompt.** After every `search_policies`
call the orchestrator applies the three-band gate. In the uncertain band it asks the judge model
whether the excerpts actually answer the question, *before* the agent writes anything, so an
unsupported answer is never streamed and then retracted. If the gate fails (reject, judge says
no, or the judge is unavailable, which fails closed):

* the excerpts are withheld, so the model cannot paraphrase a weak match into a confident answer;
* the orchestrator opens the escalation ticket itself, as a visible step, and the model only
  gets the real ticket id to relay.

That second rule exists because of a live test. A small fallback model, told "please escalate",
replied with a ticket number it had invented. Small models now only act as the judge.

**Least privilege for the model.** Account tools (`get_account_profile`, `issue_refund`,
`update_account_field`) are not offered until the session is verified. The executor would refuse
them anyway; hiding them also saves ~700 prompt tokens per call. Chunk and document ids are
stripped from excerpts (the model cited them as `【refund-policy:001】`), and citations are
attached to `turn.completed` from the retrieval metadata instead.

### Free-tier model routing

Groq's free tier allows ~8k tokens per minute *per model*, and one tool-using turn sends 2-4 model
calls of ~1.5-2.5k tokens each. `llm/router.py` treats the chain as a pool:

* **Skip before a 429, not after.** The client reads `x-ratelimit-remaining-tokens` from every
  response and models the bucket's linear refill; a request that will not fit goes straight to the
  next model. A 429 costs ~1s, so this is visible in time to first token.
* **Fall back** on 429, quota, timeouts, 5xx, connection failures and malformed tool calls; the
  failed attempt shows up in the trace as a failed LLM step. A connection that never opened is
  retried once first (nothing was sent, so it is always safe).
* **Shared cooldowns.** Quotas are per API key, so one session's 429 spares every other session.
* **Wait or fail fast.** If every model is busy for up to 12s the router waits; otherwise the
  turn ends with an error carrying `retry_after_s`.
* **Never splice answers.** Once tokens have reached the user, a failure ends the turn honestly
  instead of continuing in a different model's voice.

All providers are reached through one OpenAI-compatible streaming client (`llm/openai_compat.py`),
so Gemini is a config change: `AGENT_LLM_CHAIN=gemini:gemini-2.5-flash,groq:openai/gpt-oss-120b`.

### Error recovery

| Failure | What happens |
|---|---|
| Tool timeout / 5xx / 429 | Retried with backoff (idempotency keys make refunds and tickets safe to retry); then the model is told to apologise and offer a ticket |
| Bad tool arguments, invalid JSON | Returned to the model as a structured error it can correct |
| Model rate-limited / down | Next model in the chain; the switch is visible in the trace |
| Every model down | `error` event (`quota_exhausted` / `llm_timeout` / `upstream_unavailable`) plus an honest reply that **restates anything already done** ("a refund of $100.00 was approved (re_...)") |
| Model loops on tools | Stopped after 8 model calls with an honest reply |
| Bug in the agent | `error` event (`internal`); the stream still ends with `turn.completed` |
| Browser disconnects mid-turn | The turn runs in its own task, finishes and is saved; the client can reload the session |

`CHAOS_ENABLED=true` lets a request inject any of these with an `X-Chaos` header, e.g.
`llm:rate_limit:once, issue_refund:timeout:once`.

### Memory and sessions

A session (Postgres: `chat_sessions`, `chat_messages`, `agent_turns`) holds two kinds of memory:

* **Structured state.** The verified customer and their tier are restated in the system prompt
  every turn, so they never scroll out of the context window.
* **Conversation.** The most recent turns that fit a 2,500-token budget. The previous turn keeps
  its tool calls and results; older turns keep only what was said.

Chat and voice turns are written to the same history with their `mode`, so a customer can switch
channels mid-conversation. Session ids are 144-bit bearer tokens that expire after 24h of
inactivity. Each session runs one turn at a time (a second message gets 409). Every turn's full
event stream is stored for the observability drawer.

### API

| Endpoint | |
|---|---|
| `POST /api/sessions` | Start a conversation and get back a `session_id` |
| `POST /api/chat/stream` | `{session_id, message, mode}` returns `text/event-stream` (`id:` = seq, `event:` = type, `data:` = JSON); keep-alive comments every 15s |
| `GET /api/sessions/{id}` | Verified customer and transcript |
| `GET /api/sessions/{id}/turns` | Per-turn traces: events, latency, tokens, cost |

## Chat UI

`apps/web` is a Next.js app with one statically prerendered page. The conversation is client
state built from the agent's event stream.

* **One reducer for every source.** `lib/conversation.ts` folds events into turn state. Live
  streams, a reloaded session (each turn's stored events are replayed) and a turn recovered after
  a dropped connection all use the same function, so they render identically. Duplicate events
  are ignored by `seq`, and the server's `final_text` overrides any tokens that were missed.
* **Live steps.** Every model call, tool and judge appears as it starts, with its timing and
  result. Fallbacks show as a failed model step, followed by the backup model. Guardrail-triggered
  tickets are tagged *auto: low confidence*. Finished turns collapse to a one-line summary.
* **Grounding is visible.** Badges show the guardrail decision and confidence. Escalations show
  as a ticket card, and the sources are listed under the answer. Each turn shows time to first
  token, tokens, models and list-price cost.
* **Errors.** Server `error` events become a retry button that counts down `retry_after_s`. A
  dropped stream switches the turn to *recovering* and polls the saved traces: the server always
  finishes the turn, so the reply isn't lost. `409` (a reply is still running), `404` (session
  expired) and `429` get specific messages.
* **Demo controls.** The persona picker (`/api/demo/personas`) drops a ready-made opening message
  with credentials into the composer. With `CHAOS_ENABLED=true` a header menu sends `X-Chaos`
  presets: model rate-limited, refund tool timeout, knowledge base down, all models down.
* **Wake-up.** The page pings `/healthz` on load, which wakes a sleeping free-tier API. Session
  creation also retries until the host is up.

Model output is rendered with `react-markdown`, without raw HTML. The SSE reader is a small
`fetch`-based parser (`lib/sse.ts`), because `EventSource` cannot POST.

## Local development

Prerequisites: Docker, Node 24+, [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env          # add GROQ_API_KEY (free) for agent turns
npm install                   # web + shared-types workspaces
(cd apps/api && uv sync)      # Python deps

npm run db:up                 # Postgres + pgvector on localhost:55432
npm run db:migrate            # create tables + indexes
npm run db:seed               # load the demo customers (re-run any time to reset)
npm run kb:ingest             # chunk + embed + index (first run downloads the model, ~130 MB)
npm run kb:eval               # optional: retrieval metrics + gate calibration
npm run agent:chat -- "hi, I need a refund"   # talk to the agent in the terminal (real models)
npm run dev:api               # http://localhost:8000  (/docs, /readyz, /api/chat/stream, ...)
npm run dev:web               # http://localhost:3000
```

Checks (same as CI):

```bash
cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
npm run lint && npm run typecheck && npm run test:web && npm run build:web
npm run gen:types && git diff --exit-code packages/shared-types
```
