import type { AgentEvent } from "@support/shared-types";
import { describe, expect, it } from "vitest";

import { applyEvent, newTurn } from "./conversation";
import { buildTrace, formatMs, percentile, sessionStats } from "./trace";

const T0 = Date.parse("2026-11-26T10:00:00.000Z");
let seq = 0;
function ev(offsetMs: number, fields: Record<string, unknown>): AgentEvent {
  return {
    session_id: "sess_x",
    turn_id: "turn_1",
    seq: seq++,
    ts: new Date(T0 + offsetMs).toISOString(),
    ...fields,
  } as unknown as AgentEvent;
}

function events(): AgentEvent[] {
  seq = 0;
  const usage = (model: string, purpose: string, inTok: number, outTok: number, cost: number) => ({
    provider: "groq",
    model,
    purpose,
    input_tokens: inTok,
    output_tokens: outTok,
    cached_input_tokens: 0,
    latency_ms: 500,
    cost_usd_list_price: cost,
    cost_usd_actual: 0,
  });
  return [
    ev(0, {
      type: "turn.started",
      mode: "voice",
      user_text: "q",
      interrupted_after: null,
      voice_input: { stt_model: "whisper", stt_ms: 700, audio_s: 2.5 },
    }),
    ev(5, { type: "step.started", step_id: "a", kind: "llm", name: "llm", label: "Thinking...", input_preview: null }),
    ev(10, { type: "step.completed", step_id: "a", kind: "llm", name: "groq:primary", status: "error", duration_ms: 5, summary: "rate_limited", output_preview: null }),
    ev(12, { type: "step.started", step_id: "b", kind: "llm", name: "llm", label: "Switching...", input_preview: null }),
    ev(900, { type: "step.completed", step_id: "b", kind: "llm", name: "groq:backup", status: "ok", duration_ms: 888, summary: "-> search", output_preview: null }),
    ev(910, { type: "step.started", step_id: "c", kind: "tool", name: "search_policies", label: "Searching...", input_preview: { query: "x" } }),
    ev(980, { type: "step.completed", step_id: "c", kind: "tool", name: "search_policies", status: "ok", duration_ms: 70, summary: "reject", output_preview: null }),
    ev(985, { type: "guardrail.result", retrieval_score: 0.4, grounding_score: null, policy_ok: false, confidence: 0.4, decision: "escalate", reasons: ["low"] }),
    ev(990, { type: "step.started", step_id: "d", kind: "tool", name: "create_escalation_ticket", label: "Escalating...", input_preview: { triggered_by: "guardrail" } }),
    ev(1005, { type: "step.completed", step_id: "d", kind: "tool", name: "create_escalation_ticket", status: "ok", duration_ms: 15, summary: "TCK-1", output_preview: null }),
    ev(1006, { type: "escalated", ticket_id: "TCK-1", reason: "r", priority: "normal" }),
    ev(1200, { type: "token", text: "Hi" }),
    ev(1400, { type: "token", text: " there" }),
    ev(1410, {
      type: "metrics",
      ttft_ms: 1200,
      total_ms: 1410,
      llm_calls: [usage("backup", "agent", 1000, 50, 0.0002), usage("backup", "agent", 1200, 30, 0.0003)],
    }),
    ev(1411, { type: "turn.completed", final_text: "Hi there", citations: [], outcome: "escalated" }),
  ];
}

describe("buildTrace", () => {
  it("lays steps out on the turn's timeline, speech-to-text before zero", () => {
    const trace = buildTrace(events());
    expect(trace.rows.map((r) => [r.id, r.kind, r.status, r.startMs, r.endMs])).toEqual([
      ["stt", "stt", "ok", -700, 0],
      ["a", "llm", "error", 5, 10],
      ["b", "llm", "ok", 12, 900],
      ["c", "tool", "ok", 910, 980],
      ["d", "tool", "ok", 990, 1005],
    ]);
    expect(trace.rows[2].name).toBe("groq:backup");
    expect(trace.rows[4].triggeredBy).toBe("guardrail");
    expect(trace.fallbacks).toBe(1);
    expect([trace.firstTokenMs, trace.lastTokenMs, trace.tokenEvents]).toEqual([1200, 1400, 2]);
    expect([trace.ttftMs, trace.totalMs]).toEqual([1200, 1410]);
    expect([trace.inputTokens, trace.outputTokens]).toEqual([2200, 80]);
    expect(trace.costListUsd).toBeCloseTo(0.0005);
    expect(trace.guardrails[0].decision).toBe("escalate");
    expect(trace.escalations[0].ticket_id).toBe("TCK-1");
  });

  it("keeps an in-flight step open while the turn is live", () => {
    const live = buildTrace(events().slice(0, 6));
    expect(live.rows.at(-1)).toMatchObject({ id: "c", status: "running", endMs: null });
    expect(live.totalMs).toBeNull();
  });
});

describe("sessionStats", () => {
  it("aggregates turns, percentiles and costs", () => {
    const turn = events().reduce(applyEvent, newTurn("k1", "q", "voice"));
    const stats = sessionStats([turn, { ...turn, key: "k2" }]);
    expect(stats).toMatchObject({
      turns: 2,
      completed: 2,
      p50TotalMs: 1410,
      inputTokens: 4400,
      fallbacks: 2,
      escalations: 2,
      toolCalls: 4,
      toolErrors: 0,
    });
    expect(stats.perTurn.map((t) => t.index)).toEqual([1, 2]);
  });
});

describe("helpers", () => {
  it("percentile matches percentile_cont", () => {
    expect(percentile([100, 200, 300, 400], 0.5)).toBe(250);
    expect(percentile([100, 200, 300, 400], 0.95)).toBeCloseTo(385);
    expect(percentile([], 0.5)).toBeNull();
  });

  it("formats durations", () => {
    expect([formatMs(85), formatMs(1234), formatMs(15_000), formatMs(null)]).toEqual([
      "85ms",
      "1.23s",
      "15.0s",
      "–",
    ]);
  });
});
