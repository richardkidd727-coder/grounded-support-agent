"use client";

import { useEffect, useState } from "react";

import { api, ApiError, type ServiceSummary } from "@/lib/api";
import { formatMs, formatUsd } from "@/lib/trace";

import { Stat } from "./charts";

const WINDOWS = [
  { hours: 1, label: "Last hour" },
  { hours: 24, label: "Last 24 hours" },
  { hours: 24 * 7, label: "Last 7 days" },
];

function pct(part: number, whole: number): string {
  return whole ? `${Math.round((part / whole) * 100)}%` : "–";
}

/** Operator view across all conversations - aggregates only, no customer text. */
export function ServiceView() {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState<ServiceSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [reloads, setReloads] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .serviceSummary(hours)
      .then((summary) => {
        if (cancelled) return;
        setData(summary);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError && err.status === 401
            ? "The service view is protected on this deployment (OBSERVABILITY_TOKEN)."
            : "Couldn't load service metrics.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [hours, reloads]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-xs">
        <select
          value={hours}
          onChange={(e) => {
            setLoading(true);
            setHours(Number(e.target.value));
          }}
          className="rounded-md border border-zinc-300 bg-white px-2 py-1 dark:border-zinc-700 dark:bg-zinc-900"
          aria-label="Time window"
        >
          {WINDOWS.map((w) => (
            <option key={w.hours} value={w.hours}>
              {w.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => {
            setLoading(true);
            setReloads((n) => n + 1);
          }}
          className="rounded-md border border-zinc-300 px-2 py-1 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
        <span className="ml-auto text-[10px] text-zinc-400">All conversations · no customer data</span>
      </div>

      {error && <p className="text-xs text-red-600">{error}</p>}

      {data && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="Turns" value={data.turns.toLocaleString()} hint={`${data.sessions} sessions · ${pct(data.voice_turns, data.turns)} voice`} />
            <Stat label="Latency p50 / p95" value={formatMs(data.total_ms.p50)} hint={`p95 ${formatMs(data.total_ms.p95)}`} />
            <Stat label="First token p50 / p95" value={formatMs(data.ttft_ms.p50)} hint={`p95 ${formatMs(data.ttft_ms.p95)}`} />
            <Stat
              label="Cost (list price)"
              value={formatUsd(data.cost_usd_list_price)}
              hint={data.cost_usd_per_1k_turns !== null ? `${formatUsd(data.cost_usd_per_1k_turns)} / 1k turns · $0 paid` : "$0 paid"}
            />
            <Stat label="Tokens in / out" value={`${(data.input_tokens / 1000).toFixed(1)}k`} hint={`${(data.output_tokens / 1000).toFixed(1)}k out`} />
            <Stat label="Model fallbacks" value={String(data.model_fallbacks)} hint="failed attempts recovered" />
            <Stat label="Escalated" value={pct(data.outcomes.escalated, data.turns)} hint={`${data.guardrails.auto_tickets} auto (low confidence)`} />
            <Stat label="Degraded" value={pct(data.outcomes.degraded, data.turns)} hint={`${data.outcomes.degraded} turns`} />
          </div>

          <section className="space-y-1">
            <h4 className="text-[11px] font-semibold tracking-wide text-zinc-500 uppercase">Grounding guardrail</h4>
            <p className="text-[11px] text-zinc-600 dark:text-zinc-400">
              {data.guardrails.answered + data.guardrails.escalated} searches gated:{" "}
              <b>{data.guardrails.answered}</b> answered from policy, <b>{data.guardrails.escalated}</b> not grounded
              (excerpts withheld). The judge model ran {data.guardrails.judge_calls} time(s) for the uncertain band.
            </p>
          </section>

          <section className="space-y-1">
            <h4 className="text-[11px] font-semibold tracking-wide text-zinc-500 uppercase">Models</h4>
            <table className="w-full text-left font-mono text-[10px] tabular-nums">
              <thead className="text-zinc-500">
                <tr className="border-b border-zinc-200 dark:border-zinc-800">
                  <th className="py-1 font-normal">model</th>
                  <th className="font-normal">purpose</th>
                  <th className="text-right font-normal">calls</th>
                  <th className="text-right font-normal">tokens</th>
                  <th className="text-right font-normal">p50</th>
                  <th className="text-right font-normal">list</th>
                </tr>
              </thead>
              <tbody className="text-zinc-700 dark:text-zinc-300">
                {data.models.map((m) => (
                  <tr key={`${m.model}-${m.purpose}`} className="border-b border-zinc-100 dark:border-zinc-900">
                    <td className="py-1">{m.model}</td>
                    <td>{m.purpose}</td>
                    <td className="text-right">{m.calls}</td>
                    <td className="text-right">{((m.input_tokens + m.output_tokens) / 1000).toFixed(1)}k</td>
                    <td className="text-right">{formatMs(m.p50_latency_ms)}</td>
                    <td className="text-right">{formatUsd(m.cost_usd_list_price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="space-y-1">
            <h4 className="text-[11px] font-semibold tracking-wide text-zinc-500 uppercase">Tools & checks</h4>
            <table className="w-full text-left font-mono text-[10px] tabular-nums">
              <thead className="text-zinc-500">
                <tr className="border-b border-zinc-200 dark:border-zinc-800">
                  <th className="py-1 font-normal">step</th>
                  <th className="text-right font-normal">calls</th>
                  <th className="text-right font-normal">errors</th>
                  <th className="text-right font-normal">p50</th>
                  <th className="text-right font-normal">p95</th>
                </tr>
              </thead>
              <tbody className="text-zinc-700 dark:text-zinc-300">
                {data.steps.map((s) => (
                  <tr key={s.name} className="border-b border-zinc-100 dark:border-zinc-900">
                    <td className="py-1">{s.name}</td>
                    <td className="text-right">{s.calls}</td>
                    <td className="text-right">
                      {s.errors > 0 ? <span className="text-[var(--viz-critical)]">✕ {s.errors}</span> : 0}
                    </td>
                    <td className="text-right">{formatMs(s.p50_ms)}</td>
                    <td className="text-right">{formatMs(s.p95_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}
