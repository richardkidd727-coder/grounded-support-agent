"use client";

import { useEffect, useMemo, useState } from "react";

import type { TurnView } from "@/lib/conversation";
import { formatMs, formatUsd, sessionStats } from "@/lib/trace";

import { LatencyChart, Stat } from "./charts";
import { ServiceView } from "./service-view";
import { TurnDetail } from "./turn-detail";

type Tab = "conversation" | "service";

/**
 * Slide-over drawer: traces, latency, tokens and cost.
 *
 * "This conversation" is built from the same event streams that drive the chat (live turns update
 * in place); "Service" is the server's aggregate across all conversations.
 */
export function ObservabilityDrawer({
  open,
  onClose,
  turns,
  selectedKey,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  turns: TurnView[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("conversation");
  const stats = useMemo(() => sessionStats(turns), [turns]);
  // Follow the latest turn unless one was picked.
  const index = Math.max(
    0,
    selectedKey ? turns.findIndex((t) => t.key === selectedKey) : turns.length - 1,
  );
  const turn = turns[index];

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 z-20 bg-black/20 sm:hidden" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-label="Observability"
        className="fixed inset-y-0 right-0 z-30 flex w-full flex-col border-l border-zinc-200 bg-white shadow-xl sm:w-[620px] dark:border-zinc-800 dark:bg-zinc-950"
      >
        <header className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Observability</h2>
          <div role="tablist" className="ml-3 flex rounded-lg bg-zinc-100 p-0.5 text-xs dark:bg-zinc-900">
            {(
              [
                ["conversation", "This conversation"],
                ["service", "Service"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                role="tab"
                type="button"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={`rounded-md px-2.5 py-1 ${tab === id ? "bg-white font-medium text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-100" : "text-zinc-500"}`}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close observability"
            className="ml-auto rounded-md px-2 py-1 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-900"
          >
            ✕
          </button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
          {tab === "service" ? (
            <ServiceView />
          ) : turns.length === 0 ? (
            <p className="text-sm text-zinc-500">
              Send a message - every model call, tool, guardrail check and token will show up here.
            </p>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Stat label="Turns" value={String(stats.turns)} hint={`${stats.toolCalls} tool calls`} />
                <Stat
                  label="Latency p50"
                  value={formatMs(stats.p50TotalMs)}
                  hint={`p95 ${formatMs(stats.p95TotalMs)} · first token ${formatMs(stats.p50TtftMs)}`}
                />
                <Stat
                  label="Tokens in / out"
                  value={`${(stats.inputTokens / 1000).toFixed(1)}k`}
                  hint={`${stats.outputTokens.toLocaleString()} out`}
                />
                <Stat label="Cost (list)" value={formatUsd(stats.costListUsd)} hint="$0 paid (free tier)" />
              </div>
              {(stats.fallbacks > 0 || stats.toolErrors > 0 || stats.escalations > 0) && (
                <p className="text-[11px] text-zinc-600 dark:text-zinc-400">
                  {stats.fallbacks} model fallback(s) · {stats.toolErrors} failed tool attempt(s) ·{" "}
                  {stats.escalations} escalation(s)
                </p>
              )}
              <LatencyChart stats={stats} selected={turn?.key ?? null} onSelect={onSelect} />
              {turn && <TurnDetail turn={turn} index={index + 1} nextTurn={turns[index + 1]} />}
            </>
          )}
        </div>
      </aside>
    </>
  );
}
