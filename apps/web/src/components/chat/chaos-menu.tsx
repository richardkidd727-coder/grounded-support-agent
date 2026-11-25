"use client";

/** Fault-injection presets sent as the X-Chaos header (server must run with CHAOS_ENABLED). */
export const CHAOS_PRESETS = [
  { value: "", label: "No faults" },
  { value: "llm:rate_limit:once", label: "Model rate-limited → backup model" },
  { value: "issue_refund:timeout:once", label: "Refund tool times out → retry" },
  { value: "search_policies:error", label: "Knowledge base down" },
  { value: "llm:timeout", label: "All models down → degraded reply" },
] as const;

export function ChaosMenu({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const active = value !== "";
  return (
    <label
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${
        active
          ? "border-orange-300 bg-orange-50 text-orange-800 dark:border-orange-800 dark:bg-orange-950/50 dark:text-orange-300"
          : "border-zinc-200 text-zinc-600 dark:border-zinc-800 dark:text-zinc-400"
      }`}
      title="Inject failures into the next messages to see recovery"
    >
      <span aria-hidden>⚡</span>
      <span className="sr-only">Fault injection</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="max-w-[11rem] cursor-pointer bg-transparent outline-none sm:max-w-none"
      >
        {CHAOS_PRESETS.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
      </select>
    </label>
  );
}
