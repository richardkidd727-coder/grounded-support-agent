"use client";

import { type ApiHealth, useApiHealth } from "@/lib/use-api-health";

const LABELS: Record<ApiHealth, { text: string; dot: string }> = {
  checking: { text: "Connecting…", dot: "bg-zinc-400 animate-pulse" },
  waking: { text: "Waking server (free tier, ~30s)…", dot: "bg-amber-500 animate-pulse" },
  online: { text: "Online", dot: "bg-emerald-500" },
  offline: { text: "Server unreachable", dot: "bg-red-500" },
};

export function ApiStatus() {
  const health = useApiHealth();
  const { text, dot } = LABELS[health];
  return (
    <span
      role="status"
      aria-live="polite"
      className="inline-flex items-center gap-2 rounded-full border border-zinc-200 px-3 py-1 text-xs text-zinc-600 dark:border-zinc-800 dark:text-zinc-400"
    >
      <span className={`size-2 rounded-full ${dot}`} aria-hidden />
      {text}
    </span>
  );
}
