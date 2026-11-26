"use client";

import { formatMs, type SessionStats } from "@/lib/trace";

export function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-zinc-200 px-2.5 py-2 dark:border-zinc-800">
      <div className="text-[10px] text-zinc-500">{label}</div>
      <div className="font-mono text-base font-semibold text-zinc-900 tabular-nums dark:text-zinc-100">
        {value}
      </div>
      {hint && <div className="text-[10px] text-zinc-400">{hint}</div>}
    </div>
  );
}

/**
 * Total latency per turn (one series, one hue - no legend box needed beyond naming the tick).
 * The tick inside each bar marks time to first token. Bars are buttons: click to inspect a turn.
 */
export function LatencyChart({
  stats,
  selected,
  onSelect,
}: {
  stats: SessionStats;
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  const data = stats.perTurn;
  const max = Math.max(1, ...data.map((d) => d.totalMs ?? 0));
  return (
    <figure>
      <figcaption className="mb-1 flex items-baseline justify-between text-[11px]">
        <span className="font-medium text-zinc-700 dark:text-zinc-300">Latency per turn</span>
        <span className="inline-flex items-center gap-1 text-[10px] text-zinc-500">
          <span className="h-px w-3 bg-zinc-900 dark:bg-zinc-100" aria-hidden /> first token
        </span>
      </figcaption>
      <div className="relative flex h-28 items-end gap-[2px] border-b border-[var(--viz-axis)] pl-9">
        <span className="absolute top-0 left-0 font-mono text-[9px] text-zinc-400">{formatMs(max)}</span>
        <span className="absolute bottom-0 left-0 font-mono text-[9px] text-zinc-400">0</span>
        <span className="absolute inset-x-9 top-0 border-t border-[var(--viz-grid)]" aria-hidden />
        {data.map((d) => {
          const h = d.totalMs !== null ? (d.totalMs / max) * 100 : 0;
          const tick = d.ttftMs !== null && d.totalMs ? (d.ttftMs / max) * 100 : null;
          const isSelected = d.key === selected;
          // keep tooltips inside the chart: anchor right-side bars' tooltips to their right edge
          const rightHalf = d.index > data.length / 2;
          return (
            <button
              key={d.key}
              type="button"
              onClick={() => onSelect(d.key)}
              aria-pressed={isSelected}
              aria-label={`Turn ${d.index}: ${formatMs(d.totalMs)} total, first token ${formatMs(d.ttftMs)}`}
              className="group relative h-full min-w-1.5 flex-1 cursor-pointer"
            >
              <span
                className={`absolute inset-x-0 bottom-0 rounded-t-[4px] ${d.totalMs === null ? "animate-pulse" : ""} ${isSelected ? "ring-2 ring-zinc-900 ring-offset-1 dark:ring-zinc-100" : ""}`}
                style={{
                  height: `${Math.max(h, d.totalMs === null ? 6 : 2)}%`,
                  background: "var(--viz-llm)",
                  opacity: d.totalMs === null ? 0.4 : 1,
                }}
              />
              {tick !== null && (
                <span
                  className="absolute inset-x-0 h-0.5 bg-zinc-900 dark:bg-zinc-100"
                  style={{ bottom: `${tick}%` }}
                  aria-hidden
                />
              )}
              <span className={`pointer-events-none absolute bottom-full z-10 mb-1 hidden w-max ${rightHalf ? "right-0" : "left-0"} rounded-md border border-zinc-200 bg-white px-2 py-1 text-[10px] text-zinc-700 shadow-md group-hover:block dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200`}>
                Turn {d.index} · {formatMs(d.totalMs)} · first token {formatMs(d.ttftMs)}
              </span>
            </button>
          );
        })}
      </div>
      <div className="flex gap-[2px] pl-9 font-mono text-[9px] text-zinc-400">
        {data.map((d) => (
          <span key={d.key} className="min-w-1.5 flex-1 text-center">
            {data.length <= 20 || d.index % 5 === 0 ? d.index : ""}
          </span>
        ))}
      </div>
    </figure>
  );
}
