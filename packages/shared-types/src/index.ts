export * from "./generated/agent-event";

import type { AgentEvent } from "./generated/agent-event";

/** Narrow an AgentEvent by its `type` discriminator. */
export type EventOfType<T extends AgentEvent["type"]> = Extract<AgentEvent, { type: T }>;

export const isEventType = <T extends AgentEvent["type"]>(
  event: AgentEvent,
  type: T,
): event is EventOfType<T> => event.type === type;
