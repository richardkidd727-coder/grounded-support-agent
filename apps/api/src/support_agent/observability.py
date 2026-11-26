"""Service-level metrics aggregated from stored turn traces.

Every turn's full event stream is already persisted (`agent_turns.events`) for the per-session
trace view; this module answers the operator's questions across all sessions with plain SQL over
that JSON - no separate metrics pipeline to keep in sync. Aggregates only: no customer text,
session ids or tool arguments leave this module.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import text

from support_agent.db import Database


class Percentiles(BaseModel):
    p50: float | None
    p95: float | None


class Outcomes(BaseModel):
    answered: int
    escalated: int
    degraded: int


class ModelUsage(BaseModel):
    provider: str
    model: str
    purpose: str
    calls: int
    input_tokens: int
    output_tokens: int
    p50_latency_ms: float | None
    cost_usd_list_price: float


class StepUsage(BaseModel):
    name: str
    kind: str
    calls: int
    errors: int
    p50_ms: float | None
    p95_ms: float | None


class Guardrails(BaseModel):
    answered: int  # searches whose grounding passed
    escalated: int  # searches that failed the gate (excerpts withheld)
    judge_calls: int
    auto_tickets: int  # tickets opened by the guardrail rather than the model


class ServiceSummary(BaseModel):
    window_hours: int
    since: datetime
    sessions: int
    turns: int
    voice_turns: int
    outcomes: Outcomes
    total_ms: Percentiles
    ttft_ms: Percentiles
    input_tokens: int
    output_tokens: int
    cost_usd_list_price: float
    cost_usd_per_1k_turns: float | None
    model_fallbacks: int  # failed model attempts that fell through to the next model
    models: list[ModelUsage]
    steps: list[StepUsage]
    guardrails: Guardrails


_TURNS = text("""
    SELECT count(*) AS turns,
           count(DISTINCT session_id) AS sessions,
           count(*) FILTER (WHERE mode = 'voice') AS voice_turns,
           count(*) FILTER (WHERE outcome = 'answered') AS answered,
           count(*) FILTER (WHERE outcome = 'escalated') AS escalated,
           count(*) FILTER (WHERE outcome = 'degraded') AS degraded,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms) AS total_p50,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms) AS total_p95,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY ttft_ms) AS ttft_p50,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms) AS ttft_p95,
           coalesce(sum(input_tokens), 0) AS input_tokens,
           coalesce(sum(output_tokens), 0) AS output_tokens,
           coalesce(sum(cost_usd_list_price), 0) AS cost
    FROM agent_turns WHERE started_at >= :since
""")

_MODELS = text("""
    SELECT c->>'provider' AS provider, c->>'model' AS model, c->>'purpose' AS purpose,
           count(*) AS calls,
           sum((c->>'input_tokens')::int) AS input_tokens,
           sum((c->>'output_tokens')::int) AS output_tokens,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY (c->>'latency_ms')::int) AS p50_latency_ms,
           sum((c->>'cost_usd_list_price')::numeric) AS cost
    FROM agent_turns t
    CROSS JOIN LATERAL jsonb_array_elements(t.events) e
    CROSS JOIN LATERAL jsonb_array_elements(e->'llm_calls') c
    WHERE t.started_at >= :since AND e->>'type' = 'metrics'
    GROUP BY 1, 2, 3
    ORDER BY calls DESC
""")

_STEPS = text("""
    SELECT e->>'name' AS name, e->>'kind' AS kind, count(*) AS calls,
           count(*) FILTER (WHERE e->>'status' = 'error') AS errors,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY (e->>'duration_ms')::int) AS p50_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY (e->>'duration_ms')::int) AS p95_ms
    FROM agent_turns t
    CROSS JOIN LATERAL jsonb_array_elements(t.events) e
    WHERE t.started_at >= :since AND e->>'type' = 'step.completed'
      AND e->>'kind' IN ('tool', 'guardrail')
    GROUP BY 1, 2
    ORDER BY calls DESC
""")

_SIGNALS = text("""
    SELECT
      count(*) FILTER (WHERE e->>'type' = 'step.completed' AND e->>'kind' = 'llm'
                         AND e->>'status' = 'error') AS fallbacks,
      count(*) FILTER (WHERE e->>'type' = 'guardrail.result'
                         AND e->>'decision' = 'answer') AS guard_answer,
      count(*) FILTER (WHERE e->>'type' = 'guardrail.result'
                         AND e->>'decision' = 'escalate') AS guard_escalate,
      count(*) FILTER (WHERE e->>'type' = 'step.started'
                         AND e->>'name' = 'grounding_judge') AS judge_calls,
      count(*) FILTER (WHERE e->>'type' = 'step.started'
                         AND e->>'name' = 'create_escalation_ticket'
                         AND e->'input_preview'->>'triggered_by' = 'guardrail') AS auto_tickets
    FROM agent_turns t
    CROSS JOIN LATERAL jsonb_array_elements(t.events) e
    WHERE t.started_at >= :since
""")


def _f(value: object) -> float | None:
    return None if value is None else round(float(value), 1)  # type: ignore[arg-type]


async def service_summary(db: Database, *, now: datetime, window_hours: int) -> ServiceSummary:
    since = now - timedelta(hours=window_hours)
    params = {"since": since}
    async with db.engine.connect() as conn:
        t = (await conn.execute(_TURNS, params)).one()
        models = (await conn.execute(_MODELS, params)).all()
        steps = (await conn.execute(_STEPS, params)).all()
        s = (await conn.execute(_SIGNALS, params)).one()

    cost = float(t.cost)
    return ServiceSummary(
        window_hours=window_hours,
        since=since,
        sessions=t.sessions,
        turns=t.turns,
        voice_turns=t.voice_turns,
        outcomes=Outcomes(answered=t.answered, escalated=t.escalated, degraded=t.degraded),
        total_ms=Percentiles(p50=_f(t.total_p50), p95=_f(t.total_p95)),
        ttft_ms=Percentiles(p50=_f(t.ttft_p50), p95=_f(t.ttft_p95)),
        input_tokens=int(t.input_tokens),
        output_tokens=int(t.output_tokens),
        cost_usd_list_price=round(cost, 8),
        cost_usd_per_1k_turns=round(cost / t.turns * 1000, 6) if t.turns else None,
        model_fallbacks=s.fallbacks or 0,
        models=[
            ModelUsage(
                provider=m.provider,
                model=m.model,
                purpose=m.purpose,
                calls=m.calls,
                input_tokens=int(m.input_tokens or 0),
                output_tokens=int(m.output_tokens or 0),
                p50_latency_ms=_f(m.p50_latency_ms),
                cost_usd_list_price=round(float(m.cost or 0), 6),
            )
            for m in models
        ],
        steps=[
            StepUsage(
                name=r.name,
                kind=r.kind,
                calls=r.calls,
                errors=r.errors,
                p50_ms=_f(r.p50_ms),
                p95_ms=_f(r.p95_ms),
            )
            for r in steps
        ],
        guardrails=Guardrails(
            answered=s.guard_answer or 0,
            escalated=s.guard_escalate or 0,
            judge_calls=s.judge_calls or 0,
            auto_tickets=s.auto_tickets or 0,
        ),
    )
