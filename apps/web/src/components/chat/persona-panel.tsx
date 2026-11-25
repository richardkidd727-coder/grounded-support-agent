"use client";

import { useEffect, useState } from "react";

import { api, type PersonaCard } from "@/lib/api";

const TIER_STYLE: Record<PersonaCard["tier"], string> = {
  free: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  pro: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  enterprise: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
};

export function credentialsSentence(p: PersonaCard): string {
  const factor = p.card_last4
    ? `my card ends in ${p.card_last4}`
    : `my invoice number is ${p.invoice_id}`;
  return `My email is ${p.email} and ${factor}.`;
}

/**
 * Demo customers (DEMO_MODE only). Each exercises a different policy branch; "Use" drops a
 * ready-made opening message into the composer, credentials included.
 */
export function PersonaPanel({ onUse }: { onUse: (text: string) => void }) {
  const [personas, setPersonas] = useState<PersonaCard[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .personas()
      .then((p) => !cancelled && setPersonas(p))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section aria-labelledby="personas-heading" className="space-y-3">
      <div>
        <h2 id="personas-heading" className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          Demo customers
        </h2>
        <p className="text-xs text-zinc-500">
          Fictional accounts. Each one triggers a different refund or policy path.
        </p>
      </div>
      {failed && <p className="text-xs text-red-600">Couldn&apos;t load demo customers.</p>}
      {!personas && !failed && <p className="text-xs text-zinc-400">Loading…</p>}
      <ul className="space-y-2">
        {personas?.map((p) => (
          <li
            key={p.email}
            className="rounded-xl border border-zinc-200 bg-white p-3 text-xs dark:border-zinc-800 dark:bg-zinc-900"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-zinc-900 dark:text-zinc-100">{p.name}</span>
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${TIER_STYLE[p.tier]}`}>
                {p.tier}
              </span>
            </div>
            <p className="mt-1 text-zinc-500">{p.scenario}</p>
            <p className="mt-1 font-mono text-[10px] text-zinc-400">
              {p.email} · {p.card_last4 ? `card ••${p.card_last4}` : p.invoice_id}
            </p>
            <button
              type="button"
              onClick={() => onUse(`${p.try_saying} ${credentialsSentence(p)}`)}
              className="mt-2 rounded-md border border-zinc-300 px-2 py-1 font-medium text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              Use this customer
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
