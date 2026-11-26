"use client";

import { formatMs, type RowKind, type TurnTrace, type WaterfallRow } from "@/lib/trace";

const COLOR: Record<RowKind, string> = {
  llm: "var(--viz-llm)",
  tool: "var(--viz-tool)",
  retrieval: "var(--viz-tool)",
  guardrail: "var(--viz-guardrail)",
  stt: "var(--viz-stt)",
};

const KIND_LABEL: Record<RowKind, string> = {
  llm: "Model",
  tool: "Tool",
  retrieval: "Search",
  guardrail: "Guardrail",
  stt: "Speech to text",
};

interface Bar {
  row: WaterfallRow;
  end: number;
  streaming?: boolean;
}

/**
 * Gantt-style waterfall of one turn: every model call, tool, guardrail check (and, for voice,
 * speech-to-text before the turn started) on a shared time axis, with the first-token marker.
 * Rows are direct-labelled, so color is never the only cue; failures add ✕ and a label.
 */
export function Waterfall({ trace, live }: { trace: TurnTrace; live: boolean }) {
  // "now" for a running step = the latest thing we have heard about (deterministic, no clock).
  const latest = Math.max(
    0,
    trace.lastTokenMs ?? 0,
    ...trace.rows.map((r) => r.endMs ?? r.startMs),
  );
  const bars: Bar[] = trace.rows.map((row) => ({ row, end: row.endMs ?? latest }));
  if (trace.firstTokenMs !== null && trace.lastTokenMs !== null) {
    bars.push({
      row: {
        id: "stream",
        kind: "llm",
        name: `${trace.tokenEvents} token events`,
        label: "Streaming the answer",
        status: live ? "running" : "ok",
        startMs: trace.firstTokenMs,
        endMs: trace.lastTokenMs,
        summary: null,
        triggeredBy: null,
      },
      end: Math.max(trace.lastTokenMs, trace.firstTokenMs + 1),
      streaming: true,
    });
  }
  if (!bars.length) return <p className="text-xs text-zinc-500">No steps yet.</p>;

  const min = Math.min(0, ...bars.map((b) => b.row.startMs));
  const max = Math.max(trace.totalMs ?? 0, latest, ...bars.map((b) => b.end), min + 1);
  const span = max - min;
  const pct = (ms: number) => ((ms - min) / span) * 100;
  // Few, meaningful ticks (they must fit a phone): zero always, so pre-turn speech-to-text reads
  // as "before the turn started".
  const ticks = min < 0 ? [min, 0, max] : [0, max / 2, max];

  return (
    <figure className="space-y-1">
      <div className="grid grid-cols-[9.5rem_1fr] gap-x-2 text-[10px] text-zinc-400">
        <span />
        <div className="relative h-3">
          {ticks.map((t, i) => (
            <span
              key={i}
              className="absolute -translate-x-1/2 font-mono first:translate-x-0 last:-translate-x-full"
              style={{ left: `${pct(t)}%` }}
            >
              {formatMs(t)}
            </span>
          ))}
        </div>
      </div>

      <ol className="relative space-y-1">
        {bars.map(({ row, end, streaming }) => {
          const failed = row.status === "error";
          const left = pct(row.startMs);
          const width = Math.max(pct(end) - left, 0.6);
          const duration = end - row.startMs;
          return (
            <li key={row.id} className="grid grid-cols-[9.5rem_1fr] items-center gap-x-2 text-[11px]">
              <div className="min-w-0 leading-tight">
                <div className="truncate text-zinc-800 dark:text-zinc-200" title={row.label}>
                  {failed && <span className="mr-0.5 font-bold text-[var(--viz-critical)]">✕</span>}
                  {row.label.replace(/\.\.\.$/, "")}
                </div>
                <div className="truncate font-mono text-[9px] text-zinc-400" title={row.name}>
                  {failed ? "failed · " : ""}
                  {row.triggeredBy ? `auto (${row.triggeredBy}) · ` : ""}
                  {row.name}
                </div>
              </div>
              <div className="group relative h-5">
                {ticks.map((t, i) => (
                  <span
                    key={i}
                    className="absolute inset-y-0 w-px bg-[var(--viz-grid)]"
                    style={{ left: `${pct(t)}%` }}
                    aria-hidden
                  />
                ))}
                <span
                  role="img"
                  aria-label={`${KIND_LABEL[row.kind]} ${row.label} ${failed ? "failed" : row.status}, ${formatMs(duration)}`}
                  className={`absolute top-1 h-3 rounded-[4px] ${row.status === "running" ? "animate-pulse" : ""}`}
                  style={{
                    left: `${left}%`,
                    width: `max(${width}%, 3px)`,
                    background: failed ? "var(--viz-critical)" : COLOR[row.kind],
                    opacity: streaming ? 0.45 : 1,
                  }}
                />
                <span
                  className="pointer-events-none absolute top-0 z-10 hidden w-max max-w-64 rounded-md border border-zinc-200 bg-white px-2 py-1 text-[10px] text-zinc-700 shadow-md group-hover:block dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
                  style={{ left: `min(${left}%, 60%)`, transform: "translateY(-105%)" }}
                >
                  <b>{KIND_LABEL[row.kind]}</b> · {formatMs(row.startMs)} → {formatMs(end)} ({formatMs(duration)})
                  {row.summary && <span className="block text-zinc-500">{row.summary}</span>}
                </span>
              </div>
            </li>
          );
        })}
        {trace.ttftMs !== null && (
          <li
            aria-hidden
            className="pointer-events-none absolute inset-y-0 right-0 left-[calc(9.5rem+0.5rem)]"
          >
            <span
              className="absolute inset-y-0 border-l border-dashed border-zinc-500 dark:border-zinc-400"
              style={{ left: `${pct(trace.ttftMs)}%` }}
            />
          </li>
        )}
      </ol>

      <figcaption className="flex flex-wrap gap-x-3 gap-y-1 pt-1 text-[10px] text-zinc-500">
        {(["llm", "tool", "guardrail", "stt"] as const).map((k) => (
          <span key={k} className="inline-flex items-center gap-1">
            <span className="h-2 w-3 rounded-[2px]" style={{ background: COLOR[k] }} aria-hidden />
            {KIND_LABEL[k]}
          </span>
        ))}
        <span className="inline-flex items-center gap-1">
          <span className="font-bold text-[var(--viz-critical)]" aria-hidden>
            ✕
          </span>
          Failed attempt
        </span>
        {trace.ttftMs !== null && (
          <span className="inline-flex items-center gap-1">
            <span className="h-3 border-l border-dashed border-zinc-500" aria-hidden />
            First token {formatMs(trace.ttftMs)}
          </span>
        )}
      </figcaption>
    </figure>
  );
}
