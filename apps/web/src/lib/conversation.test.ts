import type { AgentEvent } from "@support/shared-types";
import { describe, expect, it } from "vitest";

import type { TurnTrace } from "./api";
import { activeStep, applyEvent, conversationReducer, isBusy, newTurn, turnFromTrace } from "./conversation";

const base = { session_id: "sess_x", turn_id: "turn_1", ts: "2026-11-25T12:00:00Z" };
let seq = 0;
function ev<T extends AgentEvent["type"]>(
  type: T,
  fields: Omit<Extract<AgentEvent, { type: T }>, "type" | "seq" | "session_id" | "turn_id" | "ts">,
): AgentEvent {
  return { ...base, seq: seq++, type, ...fields } as unknown as AgentEvent;
}

function liveTurn(): AgentEvent[] {
  seq = 0;
  return [
    ev("turn.started", { mode: "chat", user_text: "refund?", interrupted_after: null }),
    ev("step.started", { step_id: "s1", kind: "llm", name: "llm", label: "Thinking...", input_preview: null }),
    ev("step.completed", {
      step_id: "s1",
      kind: "llm",
      name: "groq:openai/gpt-oss-120b",
      status: "ok",
      duration_ms: 900,
      summary: "-> search_policies",
      output_preview: null,
    }),
    ev("step.started", {
      step_id: "s2",
      kind: "tool",
      name: "search_policies",
      label: "Searching knowledge base...",
      input_preview: { query: "refund" },
    }),
    ev("step.completed", {
      step_id: "s2",
      kind: "tool",
      name: "search_policies",
      status: "ok",
      duration_ms: 80,
      summary: "confident match",
      output_preview: null,
    }),
    ev("guardrail.result", {
      retrieval_score: 0.82,
      grounding_score: null,
      policy_ok: true,
      confidence: 0.82,
      decision: "answer",
      reasons: ["similarity 0.82"],
    }),
    ev("token", { text: "Within " }),
    ev("token", { text: "30 days." }),
    ev("metrics", { ttft_ms: 1200, total_ms: 1500, llm_calls: [] }),
    ev("turn.completed", {
      final_text: "Within 30 days.",
      citations: [
        { chunk_id: "c1", doc_id: "refund-policy", title: "Refund Policy", section_path: "Refund Policy > Pro", score: 0.82 },
      ],
      outcome: "answered",
    }),
  ];
}

describe("applyEvent", () => {
  it("builds a turn from the live event stream", () => {
    const events = liveTurn();
    const mid = events.slice(0, 4).reduce(applyEvent, newTurn("k", "refund?", "chat"));
    expect(mid.status).toBe("streaming");
    expect(activeStep(mid)?.label).toBe("Searching knowledge base...");
    expect(mid.steps[0].name).toBe("groq:openai/gpt-oss-120b"); // completion names the model

    const done = events.reduce(applyEvent, newTurn("k", "refund?", "chat"));
    expect(done.status).toBe("done");
    expect(done.text).toBe("Within 30 days.");
    expect(done.turnId).toBe("turn_1");
    expect(done.guardrails[0].decision).toBe("answer");
    expect(done.citations[0].section_path).toBe("Refund Policy > Pro");
    expect(activeStep(done)).toBeNull();
    expect(isBusy(done)).toBe(false);
  });

  it("ignores duplicate or out-of-date events", () => {
    const events = liveTurn();
    const once = events.reduce(applyEvent, newTurn("k", "q", "chat"));
    const twice = events.reduce(applyEvent, once);
    expect(twice).toBe(once);
  });

  it("uses the server's final text even if tokens were missed", () => {
    const events = liveTurn().filter((e) => e.type !== "token");
    expect(events.reduce(applyEvent, newTurn("k", "q", "chat")).text).toBe("Within 30 days.");
  });

  it("does not leave a step spinning when its completion was lost", () => {
    const events = liveTurn().filter((e) => !(e.type === "step.completed" && e.step_id === "s2"));
    const done = events.reduce(applyEvent, newTurn("k", "q", "chat"));
    expect(done.steps.find((s) => s.id === "s2")?.status).toBe("skipped");
  });

  it("records server errors with retry information", () => {
    seq = 0;
    const turn = applyEvent(
      newTurn("k", "q", "chat"),
      ev("error", { code: "quota_exhausted", message: "busy", retryable: true, retry_after_s: 12 }),
    );
    expect(turn.error).toEqual({ code: "quota_exhausted", message: "busy", retryable: true, retryAfterS: 12 });
  });
});

describe("conversationReducer", () => {
  it("replays a saved trace into the same state as the live stream", () => {
    const events = liveTurn();
    const trace = {
      turn_id: "turn_1",
      mode: "chat",
      user_text: "refund?",
      events,
    } as unknown as TurnTrace;
    const live = events.reduce(applyEvent, newTurn("k", "refund?", "chat"));
    expect(turnFromTrace("k", trace)).toEqual(live);

    let turns = conversationReducer([], { type: "start", key: "k", userText: "refund?", mode: "chat" });
    turns = conversationReducer(turns, { type: "status", key: "k", status: "recovering" });
    expect(isBusy(turns[0])).toBe(true);
    turns = conversationReducer(turns, { type: "replace", key: "k", trace });
    expect(turns[0]).toEqual(live);
  });

  it("marks a rejected turn as failed with the client error", () => {
    let turns = conversationReducer([], { type: "start", key: "k", userText: "hi", mode: "voice" });
    const error = { code: "client" as const, message: "busy", retryable: true, retryAfterS: null };
    turns = conversationReducer(turns, { type: "status", key: "k", status: "failed", error });
    expect(turns[0]).toMatchObject({ status: "failed", error, mode: "voice" });
  });
});
