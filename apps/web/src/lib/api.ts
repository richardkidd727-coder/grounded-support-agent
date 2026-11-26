/** Typed client for the FastAPI backend. */

import type { AgentEvent } from "@support/shared-types";

import { API_URL } from "./config";
import { parseAgentEvent } from "./events";
import { readSse } from "./sse";

export type Mode = "chat" | "voice";

export interface PersonaCard {
  name: string;
  email: string;
  company: string;
  tier: "free" | "pro" | "enterprise";
  card_last4: string | null;
  invoice_id: string | null;
  scenario: string;
  try_saying: string;
}

export interface DemoConfig {
  chaos_enabled: boolean;
}

/** A recorded turn: the full event stream, replayable into the same UI state as a live turn. */
export interface TurnTrace {
  turn_id: string;
  mode: Mode;
  user_text: string;
  final_text: string;
  outcome: "answered" | "escalated" | "degraded";
  started_at: string;
  total_ms: number;
  ttft_ms: number | null;
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd_list_price: number;
  events: AgentEvent[];
}

export interface VoiceConfig {
  stt: boolean;
  tts: "server" | "browser";
  stt_model: string;
  tts_voice: string | null;
}

export interface Transcript {
  text: string;
  language: string | null;
  duration_s: number;
  latency_ms: number;
}

export interface SessionView {
  session_id: string;
  created_at: string;
  turn_count: number;
  customer: { name: string | null; tier: string | null } | null;
}

/** Non-2xx response, with what the UI needs to react (status, server detail, Retry-After). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly retryAfterS: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

async function toApiError(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "detail" in body) {
      const d = (body as { detail: unknown }).detail;
      detail = typeof d === "string" ? d : JSON.stringify(d);
    }
  } catch {
    // not JSON
  }
  const retryAfter = Number(res.headers.get("Retry-After"));
  return new ApiError(res.status, detail, Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null);
}

export const api = {
  createSession: () =>
    request<{ session_id: string }>("/api/sessions", { method: "POST" }).then((r) => r.session_id),

  session: (sessionId: string) =>
    request<SessionView>(`/api/sessions/${encodeURIComponent(sessionId)}`),

  /** Turn traces, oldest first. Throws ApiError(404) for unknown or expired sessions. */
  turns: (sessionId: string) =>
    request<TurnTrace[]>(`/api/sessions/${encodeURIComponent(sessionId)}/turns`).then((t) =>
      [...t].reverse(),
    ),

  personas: () => request<PersonaCard[]>("/api/demo/personas"),

  demoConfig: () => request<DemoConfig>("/api/demo/config"),

  voiceConfig: () => request<VoiceConfig>("/api/voice/config"),

  /** One utterance (16 kHz WAV) -> text. Empty text means no speech was detected. */
  transcribe: (sessionId: string, wav: ArrayBuffer, signal?: AbortSignal) =>
    request<Transcript>(`/api/voice/transcribe?session_id=${encodeURIComponent(sessionId)}`, {
      method: "POST",
      headers: { "Content-Type": "audio/wav" },
      body: wav,
      signal,
    }),

  /** One sentence -> WAV. Throws ApiError(503) when server TTS is unavailable. */
  async speak(sessionId: string, text: string, signal?: AbortSignal): Promise<ArrayBuffer> {
    const res = await fetch(`${API_URL}/api/voice/speak`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, text }),
      signal,
    });
    if (!res.ok) throw await toApiError(res);
    return res.arrayBuffer();
  },

  /**
   * Send one customer message and yield the turn's events as they arrive.
   * Throws ApiError for 4xx/5xx before the stream starts (404 expired, 409 busy, ...).
   */
  async *streamTurn(
    args: {
      sessionId: string;
      message: string;
      mode: Mode;
      chaos?: string | null;
      interruptedAfter?: string | null;
    },
    signal?: AbortSignal,
  ): AsyncGenerator<AgentEvent> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    };
    if (args.chaos) headers["X-Chaos"] = args.chaos;
    const res = await fetch(`${API_URL}/api/chat/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        session_id: args.sessionId,
        message: args.message,
        mode: args.mode,
        ...(args.interruptedAfter != null ? { interrupted_after: args.interruptedAfter } : {}),
      }),
      signal,
    });
    if (!res.ok || !res.body) throw await toApiError(res);
    for await (const frame of readSse(res.body)) {
      const event = parseAgentEvent(frame.data);
      if (event) yield event;
    }
  },
};
