"use client";

import { useEffect, useState } from "react";

import { activeStep, type TurnView } from "@/lib/conversation";

import { Markdown } from "./markdown";
import { StepTimeline } from "./step-timeline";

function UserBubble({ turn }: { turn: TurnView }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-zinc-900 px-4 py-2 text-sm whitespace-pre-wrap text-white dark:bg-zinc-100 dark:text-zinc-900">
        {turn.mode === "voice" && (
          <span className="mr-1 text-xs opacity-70" title="Sent by voice">
            🎙
          </span>
        )}
        {turn.userText}
      </div>
    </div>
  );
}

function LiveStatus({ turn }: { turn: TurnView }) {
  let label: string | null = null;
  if (turn.status === "sending") label = "Sending…";
  else if (turn.status === "recovering") label = "Connection lost - fetching the reply from the server…";
  else if (turn.status === "streaming") label = activeStep(turn)?.label ?? (turn.text ? null : "Thinking…");
  if (!label) return null;
  return (
    <p role="status" aria-live="polite" className="flex items-center gap-2 text-xs text-zinc-500">
      <span className="size-1.5 animate-pulse rounded-full bg-sky-500" aria-hidden />
      {label}
    </p>
  );
}

function RetryButton({ retryAfterS, onRetry }: { retryAfterS: number | null; onRetry: () => void }) {
  const [left, setLeft] = useState(() => Math.ceil(retryAfterS ?? 0));
  useEffect(() => {
    if (left <= 0) return;
    const timer = setTimeout(() => setLeft((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [left]);
  return (
    <button
      type="button"
      onClick={onRetry}
      disabled={left > 0}
      className="rounded-md border border-red-300 px-2 py-0.5 font-medium text-red-800 hover:bg-red-100 disabled:opacity-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950"
    >
      {left > 0 ? `Retry in ${left}s` : "Retry"}
    </button>
  );
}

function Badges({ turn, heard }: { turn: TurnView; heard: string | null }) {
  const guard = turn.guardrails.at(-1);
  return (
    <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
      {guard &&
        (guard.decision === "answer" ? (
          <span
            title={guard.reasons.join("\n")}
            className="rounded-full bg-emerald-50 px-2 py-0.5 font-medium text-emerald-800 ring-1 ring-emerald-200 dark:bg-emerald-950/50 dark:text-emerald-300 dark:ring-emerald-900"
          >
            Grounded in policy · {Math.round(guard.confidence * 100)}%
          </span>
        ) : (
          <span
            title={guard.reasons.join("\n")}
            className="rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-800 ring-1 ring-amber-200 dark:bg-amber-950/50 dark:text-amber-300 dark:ring-amber-900"
          >
            Low confidence · {Math.round(guard.confidence * 100)}% → human
          </span>
        ))}
      {heard !== null && (
        <span
          title={
            heard
              ? `Customer talked over this reply after hearing: "${heard}". The agent was told.`
              : "Customer talked over this reply before hearing any of it. The agent was told."
          }
          className="rounded-full bg-sky-50 px-2 py-0.5 font-medium text-sky-800 ring-1 ring-sky-200 dark:bg-sky-950/50 dark:text-sky-300 dark:ring-sky-900"
        >
          Interrupted by customer
        </span>
      )}
      {turn.outcome === "degraded" && (
        <span className="rounded-full bg-zinc-100 px-2 py-0.5 font-medium text-zinc-600 ring-1 ring-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:ring-zinc-700">
          Degraded reply
        </span>
      )}
    </div>
  );
}

function EscalationCard({ turn }: { turn: TurnView }) {
  if (!turn.escalations.length) return null;
  return (
    <div className="mt-2 space-y-1">
      {turn.escalations.map((e) => (
        <div
          key={e.ticket_id}
          className="flex items-center gap-2 rounded-lg border border-violet-200 bg-violet-50 px-3 py-1.5 text-xs text-violet-900 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-200"
        >
          <span aria-hidden>🎫</span>
          <span>
            Escalated to a human · <span className="font-mono font-semibold">{e.ticket_id}</span> ·{" "}
            {e.priority} priority
          </span>
        </div>
      ))}
    </div>
  );
}

function Sources({ turn }: { turn: TurnView }) {
  if (!turn.citations.length) return null;
  return (
    <div className="mt-2 text-xs text-zinc-500">
      <span className="font-medium">Sources:</span>{" "}
      {turn.citations.map((c, i) => (
        <span key={c.chunk_id}>
          {i > 0 && " · "}
          <span title={`similarity ${c.score.toFixed(2)}`}>{c.section_path}</span>
        </span>
      ))}
    </div>
  );
}

function Meta({ turn, onTrace }: { turn: TurnView; onTrace?: () => void }) {
  const m = turn.metrics;
  if (!m || turn.status !== "done") return null;
  const tokens = m.llm_calls.reduce((n, c) => n + c.input_tokens + c.output_tokens, 0);
  const cost = m.llm_calls.reduce((n, c) => n + c.cost_usd_list_price, 0);
  const models = [...new Set(m.llm_calls.map((c) => c.model))];
  return (
    <p className="mt-1.5 font-mono text-[10px] text-zinc-400">
      {m.ttft_ms !== null && `first token ${(m.ttft_ms / 1000).toFixed(1)}s · `}
      total {(m.total_ms / 1000).toFixed(1)}s · {tokens.toLocaleString()} tokens · $
      {cost.toFixed(5)} at list price ($0 on free tier)
      {models.length > 0 && ` · ${models.join(", ")}`}
      {onTrace && (
        <>
          {" · "}
          <button
            type="button"
            onClick={onTrace}
            className="text-sky-700 underline-offset-2 hover:underline dark:text-sky-400"
          >
            trace
          </button>
        </>
      )}
    </p>
  );
}

export function TurnItem({
  turn,
  onRetry,
  onTrace,
  heardBeforeInterruption = null,
}: {
  turn: TurnView;
  onRetry: (turn: TurnView) => void;
  onTrace?: (turn: TurnView) => void;
  /** Set when the next (voice) turn cut this reply off: what the customer had heard. */
  heardBeforeInterruption?: string | null;
}) {
  const streaming = turn.status === "streaming" || turn.status === "sending";
  return (
    <div className="space-y-3">
      <UserBubble turn={turn} />
      <div className="flex gap-3">
        <div
          className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-sky-600 text-xs font-semibold text-white"
          aria-hidden
        >
          D
        </div>
        <div className="min-w-0 flex-1 text-sm text-zinc-900 dark:text-zinc-100">
          <StepTimeline turn={turn} />
          <LiveStatus turn={turn} />
          {turn.text && (
            <div className="leading-relaxed">
              <Markdown text={turn.text} />
              {streaming && (
                <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-zinc-400 align-text-bottom" aria-hidden />
              )}
            </div>
          )}
          {turn.error && (
            <div
              role="alert"
              className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
            >
              <span className="flex-1">{turn.error.message}</span>
              {turn.error.retryable && (
                <RetryButton retryAfterS={turn.error.retryAfterS} onRetry={() => onRetry(turn)} />
              )}
            </div>
          )}
          <EscalationCard turn={turn} />
          <Badges turn={turn} heard={heardBeforeInterruption} />
          <Sources turn={turn} />
          <Meta turn={turn} onTrace={onTrace ? () => onTrace(turn) : undefined} />
        </div>
      </div>
    </div>
  );
}
