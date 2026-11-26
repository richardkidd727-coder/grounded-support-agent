/* Generated from apps/api events/schema.py via npm run gen:types - do not edit. */

export type AgentEvent =
  | TurnStarted
  | StepStarted
  | StepCompleted
  | TokenDelta
  | GuardrailResult
  | Escalated
  | TurnMetrics
  | TurnCompleted
  | ErrorEvent;
export type StepKind = "llm" | "tool" | "retrieval" | "guardrail";
export type ErrorCode =
  | "rate_limited"
  | "quota_exhausted"
  | "llm_timeout"
  | "upstream_unavailable"
  | "tool_failed"
  | "invalid_request"
  | "internal";

export interface TurnStarted {
  /**
   * Voice barge-in: what the customer heard of the previous reply before talking over it (empty string: nothing). Null when the previous reply was not cut off.
   */
  interrupted_after: string | null;
  mode: "chat" | "voice";
  /**
   * Monotonic per turn; lets clients detect gaps/reorders.
   */
  seq: number;
  session_id: string;
  ts: string;
  turn_id: string;
  type: "turn.started";
  user_text: string;
  /**
   * Speech-to-text timing, for voice turns.
   */
  voice_input: VoiceInput | null;
}
/**
 * How a voice turn's text was obtained (measured in the browser + transcription API).
 */
export interface VoiceInput {
  /**
   * Length of the customer's speech.
   */
  audio_s: number;
  stt_model: string;
  /**
   * Transcription round trip for the final utterance.
   */
  stt_ms: number;
}
export interface StepStarted {
  input_preview: {
    [k: string]: unknown;
  } | null;
  kind: StepKind;
  /**
   * Human status line, e.g. "Verifying account tier..."
   */
  label: string;
  /**
   * Machine name, e.g. the tool name.
   */
  name: string;
  /**
   * Monotonic per turn; lets clients detect gaps/reorders.
   */
  seq: number;
  session_id: string;
  step_id: string;
  ts: string;
  turn_id: string;
  type: "step.started";
}
export interface StepCompleted {
  duration_ms: number;
  kind: StepKind;
  name: string;
  output_preview: {
    [k: string]: unknown;
  } | null;
  /**
   * Monotonic per turn; lets clients detect gaps/reorders.
   */
  seq: number;
  session_id: string;
  status: "ok" | "error" | "skipped";
  step_id: string;
  summary: string | null;
  ts: string;
  turn_id: string;
  type: "step.completed";
}
export interface TokenDelta {
  /**
   * Monotonic per turn; lets clients detect gaps/reorders.
   */
  seq: number;
  session_id: string;
  text: string;
  ts: string;
  turn_id: string;
  type: "token";
}
export interface GuardrailResult {
  confidence: number;
  decision: "answer" | "escalate";
  /**
   * Judge score, if it ran.
   */
  grounding_score: number | null;
  policy_ok: boolean;
  reasons: string[];
  /**
   * Top hybrid score, if RAG ran.
   */
  retrieval_score: number | null;
  /**
   * Monotonic per turn; lets clients detect gaps/reorders.
   */
  seq: number;
  session_id: string;
  ts: string;
  turn_id: string;
  type: "guardrail.result";
}
export interface Escalated {
  priority: "low" | "normal" | "high" | "urgent";
  reason: string;
  /**
   * Monotonic per turn; lets clients detect gaps/reorders.
   */
  seq: number;
  session_id: string;
  ticket_id: string;
  ts: string;
  turn_id: string;
  type: "escalated";
}
export interface TurnMetrics {
  llm_calls: LlmCallUsage[];
  /**
   * Monotonic per turn; lets clients detect gaps/reorders.
   */
  seq: number;
  session_id: string;
  total_ms: number;
  ts: string;
  /**
   * Time to first token.
   */
  ttft_ms: number | null;
  turn_id: string;
  type: "metrics";
}
export interface LlmCallUsage {
  cached_input_tokens: number;
  /**
   * What we actually paid (0 on free tiers).
   */
  cost_usd_actual: number;
  /**
   * Estimate at paid list prices.
   */
  cost_usd_list_price: number;
  input_tokens: number;
  latency_ms: number;
  model: string;
  output_tokens: number;
  provider: "gemini" | "groq" | "fake";
  purpose: "agent" | "judge" | "voice";
}
export interface TurnCompleted {
  citations: Citation[];
  final_text: string;
  outcome: "answered" | "escalated" | "degraded";
  /**
   * Monotonic per turn; lets clients detect gaps/reorders.
   */
  seq: number;
  session_id: string;
  ts: string;
  turn_id: string;
  type: "turn.completed";
}
export interface Citation {
  chunk_id: string;
  doc_id: string;
  score: number;
  section_path: string;
  title: string;
}
export interface ErrorEvent {
  code: ErrorCode;
  /**
   * Safe to show to the end user.
   */
  message: string;
  retry_after_s: number | null;
  retryable: boolean;
  /**
   * Monotonic per turn; lets clients detect gaps/reorders.
   */
  seq: number;
  session_id: string;
  ts: string;
  turn_id: string;
  type: "error";
}
