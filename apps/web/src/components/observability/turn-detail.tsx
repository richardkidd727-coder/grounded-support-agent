"use client";

import type { AgentEvent } from "@support/shared-types";

import type { TurnView } from "@/lib/conversation";
import { buildTrace, formatMs, formatUsd } from "@/lib/trace";

import { Waterfall } from "./waterfall";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h4 className="text-[11px] font-semibold tracking-wide text-zinc-500 uppercase">{title}</h4>
      {children}
    </section>
  );
}

function eventLine(e: AgentEvent): string {
  switch (e.type) {
    case "turn.started":
      return `${e.mode} · "${e.user_text.slice(0, 60)}"${e.voice_input ? ` · stt ${e.voice_input.stt_ms}ms` : ""}${e.interrupted_after !== null ? " · interrupted previous reply" : ""}`;
    case "step.started":
      return `${e.kind} ${e.name} - ${e.label}`;
    case "step.completed":
      return `${e.kind} ${e.name} ${e.status} ${e.duration_ms}ms${e.summary ? ` - ${e.summary}` : ""}`;
    case "guardrail.result":
      return `${e.decision} · retrieval ${e.retrieval_score ?? "-"} · judge ${e.grounding_score ?? "-"}`;
    case "escalated":
      return `${e.ticket_id} (${e.priority})`;
    case "metrics":
      return `total ${e.total_ms}ms · ttft ${e.ttft_ms ?? "-"}ms · ${e.llm_calls.length} model calls`;
    case "turn.completed":
      return `${e.outcome} · ${e.citations.length} citation(s)`;
    case "error":
      return `${e.code} · ${e.message}`;
    case "token":
      return JSON.stringify(e.text);
  }
}

function EventLog({ events, startedAt }: { events: AgentEvent[]; startedAt: number }) {
  // Collapse runs of token events into one line each; they are the bulk of the stream.
  const lines: { seq: number; offset: number; type: string; text: string }[] = [];
  for (const e of events) {
    const offset = Date.parse(e.ts) - startedAt;
    const last = lines.at(-1);
    if (e.type === "token" && last?.type === "token") {
      last.text = `${Number(last.text.split(" ")[0]) + 1} token events`;
      continue;
    }
    lines.push({
      seq: e.seq,
      offset,
      type: e.type,
      text: e.type === "token" ? "1 token events" : eventLine(e),
    });
  }
  return (
    <ol className="max-h-56 overflow-y-auto rounded-md bg-zinc-50 p-2 font-mono text-[10px] leading-relaxed text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
      {lines.map((l) => (
        <li key={l.seq} className="flex gap-2">
          <span className="w-6 shrink-0 text-right text-zinc-400">{l.seq}</span>
          <span className="w-12 shrink-0 text-right text-zinc-400">+{formatMs(l.offset)}</span>
          <span className="w-24 shrink-0 text-zinc-800 dark:text-zinc-200">{l.type}</span>
          <span className="min-w-0 break-words">{l.text}</span>
        </li>
      ))}
    </ol>
  );
}

function download(turn: TurnView) {
  const blob = new Blob([JSON.stringify(turn.events, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${turn.turnId ?? turn.key}.trace.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export function TurnDetail({ turn, index, nextTurn }: { turn: TurnView; index: number; nextTurn?: TurnView }) {
  const trace = buildTrace(turn.events);
  const live = turn.status !== "done" && turn.status !== "failed";
  const startedAt = turn.events.length ? Date.parse(turn.events[0].ts) : 0;

  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="font-semibold text-zinc-900 dark:text-zinc-100">Turn {index}</span>
          <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
            {turn.mode === "voice" ? "🎙 voice" : "chat"}
          </span>
          <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
            {live ? "in progress" : (turn.outcome ?? turn.status)}
          </span>
          {turn.turnId && <span className="font-mono text-[10px] text-zinc-400">{turn.turnId}</span>}
          {turn.events.length > 0 && (
            <button
              type="button"
              onClick={() => download(turn)}
              className="ml-auto text-[10px] text-sky-700 underline-offset-2 hover:underline dark:text-sky-400"
            >
              Download trace JSON
            </button>
          )}
        </div>
        <p className="line-clamp-2 text-xs text-zinc-600 dark:text-zinc-400">“{turn.userText}”</p>
        {nextTurn?.interruptedAfter != null && (
          <p className="text-[11px] text-sky-800 dark:text-sky-300">
            Interrupted by the customer after hearing: “{nextTurn.interruptedAfter || "nothing yet"}”
          </p>
        )}
      </header>

      <div className="grid grid-cols-4 gap-2 text-center">
        {[
          ["Total", formatMs(trace.totalMs)],
          ["First token", formatMs(trace.ttftMs)],
          ["Tokens in / out", `${trace.inputTokens.toLocaleString()} / ${trace.outputTokens.toLocaleString()}`],
          ["Cost (list)", formatUsd(trace.costListUsd)],
        ].map(([label, value]) => (
          <div key={label} className="rounded-md bg-zinc-50 py-1.5 dark:bg-zinc-900">
            <div className="text-[9px] text-zinc-500">{label}</div>
            <div className="font-mono text-xs font-semibold text-zinc-900 tabular-nums dark:text-zinc-100">
              {value}
            </div>
          </div>
        ))}
      </div>

      <Section title="Timeline">
        <Waterfall trace={trace} live={live} />
      </Section>

      {trace.guardrails.length > 0 && (
        <Section title="Grounding guardrail">
          {trace.guardrails.map((g) => (
            <div key={g.seq} className="rounded-md border border-zinc-200 p-2 text-[11px] dark:border-zinc-800">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`font-semibold ${g.decision === "answer" ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-400"}`}>
                  {g.decision === "answer" ? "✓ Answer from policy" : "⚠ Escalate (not grounded)"}
                </span>
                <span className="font-mono text-zinc-500">
                  retrieval {g.retrieval_score ?? "-"} · judge {g.grounding_score ?? "not needed"} · confidence{" "}
                  {g.confidence}
                </span>
              </div>
              <ul className="mt-1 list-disc pl-4 text-zinc-600 dark:text-zinc-400">
                {g.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          ))}
        </Section>
      )}

      {trace.llmCalls.length > 0 && (
        <Section title="Model calls">
          <div className="overflow-x-auto">
            <table className="w-full text-left font-mono text-[10px] tabular-nums">
              <thead className="text-zinc-500">
                <tr className="border-b border-zinc-200 dark:border-zinc-800">
                  <th className="py-1 pr-2 font-normal">model</th>
                  <th className="pr-2 font-normal">purpose</th>
                  <th className="pr-2 text-right font-normal">in</th>
                  <th className="pr-2 text-right font-normal">out</th>
                  <th className="pr-2 text-right font-normal">latency</th>
                  <th className="pr-2 text-right font-normal">list</th>
                  <th className="text-right font-normal">paid</th>
                </tr>
              </thead>
              <tbody className="text-zinc-700 dark:text-zinc-300">
                {trace.llmCalls.map((c, i) => (
                  <tr key={i} className="border-b border-zinc-100 dark:border-zinc-900">
                    <td className="py-1 pr-2">{c.model}</td>
                    <td className="pr-2">{c.purpose}</td>
                    <td className="pr-2 text-right">{c.input_tokens.toLocaleString()}</td>
                    <td className="pr-2 text-right">{c.output_tokens.toLocaleString()}</td>
                    <td className="pr-2 text-right">{formatMs(c.latency_ms)}</td>
                    <td className="pr-2 text-right">{formatUsd(c.cost_usd_list_price)}</td>
                    <td className="text-right">{formatUsd(c.cost_usd_actual)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-zinc-400">
            Failed attempts (rate limits, timeouts) appear on the timeline but bill nothing. List
            prices are estimates; free tiers pay $0.
          </p>
        </Section>
      )}

      {trace.escalations.length > 0 && (
        <Section title="Escalations">
          {trace.escalations.map((e) => (
            <p key={e.ticket_id} className="text-[11px] text-zinc-700 dark:text-zinc-300">
              <span className="font-mono font-semibold">{e.ticket_id}</span> · {e.priority} · {e.reason}
            </p>
          ))}
        </Section>
      )}

      <Section title={`Event stream (${turn.events.length} events)`}>
        <EventLog events={turn.events} startedAt={startedAt} />
      </Section>
    </div>
  );
}
