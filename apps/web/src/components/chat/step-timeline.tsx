"use client";

import type { StepView, TurnView } from "@/lib/conversation";

const KIND_LABEL: Record<StepView["kind"], string> = {
  llm: "model",
  tool: "tool",
  retrieval: "search",
  guardrail: "guardrail",
};

function StatusIcon({ status }: { status: StepView["status"] }) {
  if (status === "running") {
    return (
      <span
        className="size-3.5 animate-spin rounded-full border-2 border-sky-500 border-t-transparent"
        aria-label="running"
      />
    );
  }
  const common = "size-3.5 shrink-0";
  if (status === "ok") {
    return (
      <svg viewBox="0 0 16 16" className={`${common} text-emerald-600`} aria-label="done">
        <path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" strokeWidth="2" />
      </svg>
    );
  }
  if (status === "error") {
    return (
      <svg viewBox="0 0 16 16" className={`${common} text-red-600`} aria-label="failed">
        <path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" strokeWidth="2" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" className={`${common} text-zinc-400`} aria-label="skipped">
      <path d="M4 8h8" fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

function formatMs(ms: number | null): string {
  if (ms === null) return "";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function StepRow({ step }: { step: StepView }) {
  const byGuardrail = step.input?.["triggered_by"] === "guardrail";
  const failed = step.status === "error";
  return (
    <li className="flex items-start gap-2 py-0.5">
      <span className="mt-0.5 flex size-3.5 items-center justify-center">
        <StatusIcon status={step.status} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className={failed ? "text-red-700 dark:text-red-400" : undefined}>
            {step.label}
          </span>
          <span className="rounded bg-zinc-100 px-1 font-mono text-[10px] text-zinc-500 dark:bg-zinc-800">
            {KIND_LABEL[step.kind]}
            {step.kind === "llm" && step.name !== "llm" ? ` · ${step.name}` : ""}
            {step.kind === "tool" ? ` · ${step.name}` : ""}
          </span>
          {byGuardrail && (
            <span className="rounded bg-amber-100 px-1 text-[10px] font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
              auto: low confidence
            </span>
          )}
        </div>
        {step.summary && <p className="truncate text-zinc-500">{step.summary}</p>}
      </div>
      <span className="shrink-0 font-mono text-[10px] text-zinc-400">{formatMs(step.durationMs)}</span>
    </li>
  );
}

export function StepTimeline({ turn }: { turn: TurnView }) {
  if (!turn.steps.length) return null;
  const list = (
    <ol className="mt-1 space-y-0.5 text-xs text-zinc-700 dark:text-zinc-300">
      {turn.steps.map((step) => (
        <StepRow key={step.id} step={step} />
      ))}
    </ol>
  );
  if (turn.status !== "done") {
    return (
      <div className="mb-2 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-900/60">
        {list}
      </div>
    );
  }

  const tools = turn.steps.filter((s) => s.kind === "tool").length;
  const failures = turn.steps.filter((s) => s.status === "error").length;
  const total = turn.metrics?.total_ms ?? null;
  return (
    <details className="group mb-2 rounded-lg border border-zinc-200 px-3 py-1.5 text-xs dark:border-zinc-800">
      <summary className="cursor-pointer list-none text-zinc-500 select-none marker:hidden">
        <span className="inline-block transition-transform group-open:rotate-90" aria-hidden>
          ›
        </span>{" "}
        {turn.steps.length} step{turn.steps.length === 1 ? "" : "s"} · {tools} tool call
        {tools === 1 ? "" : "s"}
        {failures ? ` · ${failures} failed attempt${failures === 1 ? "" : "s"}` : ""}
        {total !== null ? ` · ${formatMs(total)}` : ""}
      </summary>
      {list}
    </details>
  );
}
