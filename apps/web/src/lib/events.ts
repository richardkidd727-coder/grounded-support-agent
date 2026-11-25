import type { AgentEvent } from "@support/shared-types";

export type { AgentEvent, EventOfType } from "@support/shared-types";
export { isEventType } from "@support/shared-types";

const EVENT_TYPES = new Set<AgentEvent["type"]>([
  "turn.started",
  "step.started",
  "step.completed",
  "token",
  "guardrail.result",
  "escalated",
  "metrics",
  "turn.completed",
  "error",
]);

/**
 * Parse one SSE `data:` payload. The server is the schema authority (Pydantic); here we only
 * guard against unknown event types so a newer server cannot crash an older client.
 */
export function parseAgentEvent(data: string): AgentEvent | null {
  try {
    const value: unknown = JSON.parse(data);
    if (
      typeof value === "object" &&
      value !== null &&
      "type" in value &&
      EVENT_TYPES.has((value as { type: AgentEvent["type"] }).type)
    ) {
      return value as AgentEvent;
    }
  } catch {
    // fall through
  }
  return null;
}
