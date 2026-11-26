/**
 * Turn traces for the observability drawer, derived purely from a turn's event stream.
 *
 * Live and restored turns carry the same events, so a trace looks identical during the stream,
 * after a reload, and after a dropped connection was recovered.
 */

import type {
  AgentEvent,
  Escalated,
  GuardrailResult,
  LlmCallUsage,
  StepKind,
  VoiceInput,
} from "@support/shared-types";

import type { TurnView } from "./conversation";

export type RowKind = StepKind | "stt";
export type RowStatus = "running" | "ok" | "error" | "skipped";

export interface WaterfallRow {
  id: string;
  kind: RowKind;
  name: string;
  label: string;
  status: RowStatus;
  startMs: number; // relative to turn.started; speech-to-text is negative (it happened before)
  endMs: number | null; // null while running
  summary: string | null;
  triggeredBy: string | null;
}

export interface TurnTrace {
  rows: WaterfallRow[];
  ttftMs: number | null;
  firstTokenMs: number | null;
  lastTokenMs: number | null;
  totalMs: number | null;
  tokenEvents: number;
  llmCalls: LlmCallUsage[];
  guardrails: GuardrailResult[];
  escalations: Escalated[];
  voice: VoiceInput | null;
  inputTokens: number;
  outputTokens: number;
  costListUsd: number;
  fallbacks: number;
}

export function buildTrace(events: AgentEvent[]): TurnTrace {
  const started = events.find((e) => e.type === "turn.started");
  const t0 = started ? Date.parse(started.ts) : events.length ? Date.parse(events[0].ts) : 0;
  const at = (e: AgentEvent) => Math.max(0, Date.parse(e.ts) - t0);

  const rows: WaterfallRow[] = [];
  const byId = new Map<string, WaterfallRow>();
  let firstToken: number | null = null;
  let lastToken: number | null = null;
  let tokenEvents = 0;
  let total: number | null = null;
  let ttft: number | null = null;
  let llmCalls: LlmCallUsage[] = [];
  const guardrails: GuardrailResult[] = [];
  const escalations: Escalated[] = [];

  const voice = started?.type === "turn.started" ? started.voice_input : null;
  if (voice) {
    rows.push({
      id: "stt",
      kind: "stt",
      name: voice.stt_model,
      label: "Speech to text",
      status: "ok",
      startMs: -voice.stt_ms,
      endMs: 0,
      summary: `${voice.audio_s.toFixed(1)}s of speech`,
      triggeredBy: null,
    });
  }

  for (const e of events) {
    switch (e.type) {
      case "step.started": {
        const triggered = e.input_preview?.["triggered_by"];
        const row: WaterfallRow = {
          id: e.step_id,
          kind: e.kind,
          name: e.name,
          label: e.label,
          status: "running",
          startMs: at(e),
          endMs: null,
          summary: null,
          triggeredBy: typeof triggered === "string" ? triggered : null,
        };
        byId.set(e.step_id, row);
        rows.push(row);
        break;
      }
      case "step.completed": {
        const row = byId.get(e.step_id);
        const end = at(e);
        if (row) {
          row.status = e.status;
          row.endMs = Math.max(end, row.startMs);
          row.summary = e.summary;
          row.name = e.name; // completion names the model that actually served the call
        } else {
          rows.push({
            id: e.step_id,
            kind: e.kind,
            name: e.name,
            label: e.name,
            status: e.status,
            startMs: Math.max(0, end - e.duration_ms),
            endMs: end,
            summary: e.summary,
            triggeredBy: null,
          });
        }
        break;
      }
      case "token":
        tokenEvents += 1;
        if (firstToken === null && e.text.trim()) firstToken = at(e);
        lastToken = at(e);
        break;
      case "guardrail.result":
        guardrails.push(e);
        break;
      case "escalated":
        escalations.push(e);
        break;
      case "metrics":
        total = e.total_ms;
        ttft = e.ttft_ms;
        llmCalls = e.llm_calls;
        break;
      case "turn.completed":
        total ??= at(e);
        for (const row of rows) if (row.status === "running") row.status = "skipped";
        break;
    }
  }

  return {
    rows,
    ttftMs: ttft ?? firstToken,
    firstTokenMs: firstToken,
    lastTokenMs: lastToken,
    totalMs: total,
    tokenEvents,
    llmCalls,
    guardrails,
    escalations,
    voice,
    inputTokens: llmCalls.reduce((n, c) => n + c.input_tokens, 0),
    outputTokens: llmCalls.reduce((n, c) => n + c.output_tokens, 0),
    costListUsd: llmCalls.reduce((n, c) => n + c.cost_usd_list_price, 0),
    fallbacks: rows.filter((r) => r.kind === "llm" && r.status === "error").length,
  };
}

/** Linear-interpolated percentile (same definition as Postgres percentile_cont). */
export function percentile(values: number[], p: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = (sorted.length - 1) * p;
  const lo = Math.floor(rank);
  const hi = Math.ceil(rank);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (rank - lo);
}

export interface SessionStats {
  turns: number;
  completed: number;
  p50TotalMs: number | null;
  p95TotalMs: number | null;
  p50TtftMs: number | null;
  inputTokens: number;
  outputTokens: number;
  costListUsd: number;
  fallbacks: number;
  escalations: number;
  toolCalls: number;
  toolErrors: number;
  perTurn: { key: string; index: number; totalMs: number | null; ttftMs: number | null }[];
}

export function sessionStats(turns: TurnView[]): SessionStats {
  const traces = turns.map((t) => buildTrace(t.events));
  const done = traces.filter((t) => t.totalMs !== null);
  const totals = done.map((t) => t.totalMs as number);
  const ttfts = done.map((t) => t.ttftMs).filter((v): v is number => v !== null);
  const tools = traces.flatMap((t) => t.rows.filter((r) => r.kind === "tool"));
  return {
    turns: turns.length,
    completed: done.length,
    p50TotalMs: percentile(totals, 0.5),
    p95TotalMs: percentile(totals, 0.95),
    p50TtftMs: percentile(ttfts, 0.5),
    inputTokens: traces.reduce((n, t) => n + t.inputTokens, 0),
    outputTokens: traces.reduce((n, t) => n + t.outputTokens, 0),
    costListUsd: traces.reduce((n, t) => n + t.costListUsd, 0),
    fallbacks: traces.reduce((n, t) => n + t.fallbacks, 0),
    escalations: traces.reduce((n, t) => n + t.escalations.length, 0),
    toolCalls: tools.length,
    toolErrors: tools.filter((r) => r.status === "error").length,
    perTurn: turns.map((t, i) => ({
      key: t.key,
      index: i + 1,
      totalMs: traces[i].totalMs,
      ttftMs: traces[i].ttftMs,
    })),
  };
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  if (Math.abs(ms) < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)}s`;
}

export function formatUsd(usd: number): string {
  if (usd === 0) return "$0";
  return usd < 0.01 ? `$${usd.toFixed(5)}` : `$${usd.toFixed(3)}`;
}
