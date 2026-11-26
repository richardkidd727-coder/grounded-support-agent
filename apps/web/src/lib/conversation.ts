/**
 * Conversation state as a pure reducer over agent events.
 *
 * Live turns, restored sessions and turns recovered after a dropped connection all go through
 * the same `applyEvent`, so a replayed trace renders exactly like the live stream did.
 */

import type {
  AgentEvent,
  Citation,
  ErrorCode,
  Escalated,
  GuardrailResult,
  StepKind,
  TurnMetrics,
} from "@support/shared-types";

import type { Mode, TurnTrace } from "./api";

export type StepStatus = "running" | "ok" | "error" | "skipped";

export interface StepView {
  id: string;
  kind: StepKind;
  name: string;
  label: string;
  status: StepStatus;
  durationMs: number | null;
  summary: string | null;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
}

export type TurnStatus =
  | "sending" // request sent, nothing back yet
  | "streaming"
  | "done"
  | "recovering" // stream dropped; the server keeps going, we poll for the saved result
  | "failed"; // never started (rejected) or could not be recovered

export interface TurnError {
  code: ErrorCode | "client";
  message: string;
  retryable: boolean;
  retryAfterS: number | null;
}

export interface TurnView {
  key: string; // stable React key (client-side id; the turn_id arrives with the first event)
  turnId: string | null;
  mode: Mode;
  userText: string;
  /** Voice: this turn cut off the previous spoken reply; what the customer had heard of it. */
  interruptedAfter: string | null;
  status: TurnStatus;
  text: string;
  steps: StepView[];
  guardrails: GuardrailResult[];
  escalations: Escalated[];
  citations: Citation[];
  outcome: "answered" | "escalated" | "degraded" | null;
  metrics: TurnMetrics | null;
  error: TurnError | null;
  lastSeq: number;
  /** Every event of the turn, in order: the raw material for the observability drawer. */
  events: AgentEvent[];
}

export type ConversationAction =
  | { type: "reset"; turns?: TurnView[] }
  | { type: "start"; key: string; userText: string; mode: Mode }
  | { type: "event"; key: string; event: AgentEvent }
  | { type: "status"; key: string; status: TurnStatus; error?: TurnError | null }
  | { type: "replace"; key: string; trace: TurnTrace };

export function newTurn(key: string, userText: string, mode: Mode): TurnView {
  return {
    key,
    turnId: null,
    mode,
    userText,
    interruptedAfter: null,
    status: "sending",
    text: "",
    steps: [],
    guardrails: [],
    escalations: [],
    citations: [],
    outcome: null,
    metrics: null,
    error: null,
    lastSeq: -1,
    events: [],
  };
}

export function applyEvent(turn: TurnView, event: AgentEvent): TurnView {
  if (event.seq <= turn.lastSeq) return turn; // duplicate or replayed
  const next: TurnView = {
    ...turn,
    lastSeq: event.seq,
    turnId: event.turn_id,
    events: [...turn.events, event],
  };

  switch (event.type) {
    case "turn.started":
      return {
        ...next,
        status: "streaming",
        mode: event.mode,
        interruptedAfter: event.interrupted_after,
      };
    case "step.started":
      return {
        ...next,
        status: "streaming",
        steps: [
          ...turn.steps,
          {
            id: event.step_id,
            kind: event.kind,
            name: event.name,
            label: event.label,
            status: "running",
            durationMs: null,
            summary: null,
            input: event.input_preview ?? null,
            output: null,
          },
        ],
      };
    case "step.completed": {
      const done = {
        status: event.status,
        durationMs: event.duration_ms,
        summary: event.summary,
        output: event.output_preview ?? null,
      } as const;
      const exists = turn.steps.some((s) => s.id === event.step_id);
      return {
        ...next,
        steps: exists
          ? turn.steps.map((s) =>
              // keep the start label; the completed name says which model actually served it
              s.id === event.step_id ? { ...s, ...done, name: event.name } : s,
            )
          : [
              ...turn.steps,
              { id: event.step_id, kind: event.kind, name: event.name, label: event.name, input: null, ...done },
            ],
      };
    }
    case "token":
      return { ...next, status: "streaming", text: turn.text + event.text };
    case "guardrail.result":
      return { ...next, guardrails: [...turn.guardrails, event] };
    case "escalated":
      return { ...next, escalations: [...turn.escalations, event] };
    case "metrics":
      return { ...next, metrics: event };
    case "error":
      return {
        ...next,
        error: {
          code: event.code,
          message: event.message,
          retryable: event.retryable,
          retryAfterS: event.retry_after_s,
        },
      };
    case "turn.completed":
      return {
        ...next,
        status: "done",
        // the server's final text is authoritative (covers any token we never saw)
        text: event.final_text,
        citations: event.citations,
        outcome: event.outcome,
        // a step still "running" can only mean its completion was lost; don't spin forever
        steps: turn.steps.map((s) => (s.status === "running" ? { ...s, status: "skipped" } : s)),
      };
  }
}

export function turnFromTrace(key: string, trace: TurnTrace): TurnView {
  return trace.events.reduce(applyEvent, newTurn(key, trace.user_text, trace.mode));
}

export function conversationReducer(turns: TurnView[], action: ConversationAction): TurnView[] {
  const update = (key: string, fn: (t: TurnView) => TurnView) =>
    turns.map((t) => (t.key === key ? fn(t) : t));

  switch (action.type) {
    case "reset":
      return action.turns ?? [];
    case "start":
      return [...turns, newTurn(action.key, action.userText, action.mode)];
    case "event":
      return update(action.key, (t) => applyEvent(t, action.event));
    case "status":
      return update(action.key, (t) => ({
        ...t,
        status: action.status,
        error: action.error === undefined ? t.error : action.error,
      }));
    case "replace":
      return update(action.key, () => turnFromTrace(action.key, action.trace));
  }
}

/** The step to show in the live status line: the most recent one still running. */
export function activeStep(turn: TurnView): StepView | null {
  for (let i = turn.steps.length - 1; i >= 0; i--) {
    if (turn.steps[i].status === "running") return turn.steps[i];
  }
  return null;
}

export function isBusy(turn: TurnView | undefined): boolean {
  return !!turn && (turn.status === "sending" || turn.status === "streaming" || turn.status === "recovering");
}
