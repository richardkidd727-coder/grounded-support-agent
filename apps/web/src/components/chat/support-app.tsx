"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiStatus } from "@/components/api-status";
import { api } from "@/lib/api";
import type { TurnView } from "@/lib/conversation";
import { useAgentSession } from "@/lib/use-agent-session";

import { ChaosMenu } from "./chaos-menu";
import { Composer } from "./composer";
import { PersonaPanel } from "./persona-panel";
import { TurnItem } from "./turn-item";

const STARTERS = [
  "What's your refund policy?",
  "My files stopped syncing on my laptop",
  "How do I export all my data?",
  "I'd like to talk to a person",
];

const STICK_THRESHOLD_PX = 80;

export function SupportApp() {
  const { phase, turns, customer, notice, busy, send, newConversation } = useAgentSession();
  const [draft, setDraft] = useState("");
  const [chaos, setChaos] = useState("");
  const [chaosEnabled, setChaosEnabled] = useState(false);
  const [showPersonas, setShowPersonas] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  useEffect(() => {
    api
      .demoConfig()
      .then((c) => setChaosEnabled(c.chaos_enabled))
      .catch(() => setChaosEnabled(false));
  }, []);

  // Follow the stream while the user is at the bottom; don't yank them back if they scrolled up.
  useEffect(() => {
    const el = scroller.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [turns]);

  const onScroll = () => {
    const el = scroller.current;
    if (el) stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX;
  };

  const submit = useCallback(
    (text: string) => {
      stickToBottom.current = true;
      setDraft("");
      void send(text, { chaos: chaos || null });
    },
    [send, chaos],
  );

  const retry = useCallback((turn: TurnView) => submit(turn.userText), [submit]);

  const applyPersona = (text: string) => {
    setDraft(text);
    setShowPersonas(false);
    document.getElementById("composer")?.focus();
  };

  const ready = phase === "ready";

  return (
    <div className="flex h-dvh flex-col bg-zinc-50 dark:bg-black">
      <header className="flex flex-wrap items-center gap-2 border-b border-zinc-200 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
        <h1 className="mr-auto text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
          Driftwood Support
        </h1>
        {customer && (
          <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-800 ring-1 ring-emerald-200 dark:bg-emerald-950/50 dark:text-emerald-300 dark:ring-emerald-900">
            ✓ {customer.name}
            {customer.tier ? ` · ${customer.tier}` : ""}
          </span>
        )}
        {chaosEnabled && <ChaosMenu value={chaos} onChange={setChaos} />}
        <ApiStatus />
        <button
          type="button"
          onClick={() => setShowPersonas((v) => !v)}
          className="rounded-full border border-zinc-200 px-2.5 py-1 text-xs text-zinc-600 lg:hidden dark:border-zinc-800 dark:text-zinc-400"
          aria-expanded={showPersonas}
          aria-controls="persona-drawer"
        >
          Demo customers
        </button>
        <button
          type="button"
          onClick={() => void newConversation()}
          disabled={!ready}
          className="rounded-full border border-zinc-200 px-2.5 py-1 text-xs text-zinc-600 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-900"
        >
          New conversation
        </button>
      </header>

      <div className="relative flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col">
          <div ref={scroller} onScroll={onScroll} className="flex-1 overflow-y-auto">
            <div role="log" aria-label="Conversation" className="mx-auto max-w-3xl space-y-6 px-4 py-6">
              {phase === "unavailable" && (
                <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950/40 dark:text-red-300">
                  Can&apos;t reach the support server right now. Please refresh in a minute.
                </p>
              )}
              {ready && turns.length === 0 && (
                <div className="py-10 text-center">
                  <p className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">
                    Hi, I&apos;m Drift. How can I help?
                  </p>
                  <p className="mt-1 text-sm text-zinc-500">
                    I can answer policy questions, check your account, issue refunds within policy
                    and hand you to a human when needed.
                  </p>
                  <div className="mt-5 flex flex-wrap justify-center gap-2">
                    {STARTERS.map((s) => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => submit(s)}
                        className="rounded-full border border-zinc-300 bg-white px-3 py-1.5 text-xs text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {turns.map((turn) => (
                <TurnItem key={turn.key} turn={turn} onRetry={retry} />
              ))}
            </div>
          </div>

          <div className="border-t border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
            <div className="mx-auto max-w-3xl px-4 py-3">
              {notice && <p className="mb-2 text-xs text-red-600">{notice}</p>}
              <Composer
                value={draft}
                onChange={setDraft}
                onSubmit={() => submit(draft)}
                disabled={!ready}
                busy={busy}
              />
              <p className="mt-1.5 text-center text-[10px] text-zinc-400">
                Demo with fictional data. Answers come from Driftwood&apos;s policies; low-confidence
                questions go to a human.
              </p>
            </div>
          </div>
        </main>

        <aside
          id="persona-drawer"
          className={`${showPersonas ? "absolute inset-0 z-10 block" : "hidden"} w-full overflow-y-auto border-l border-zinc-200 bg-zinc-50 p-4 lg:static lg:block lg:w-80 dark:border-zinc-800 dark:bg-zinc-950`}
        >
          <button
            type="button"
            onClick={() => setShowPersonas(false)}
            className="mb-3 rounded-full border border-zinc-300 px-2.5 py-1 text-xs text-zinc-600 lg:hidden dark:border-zinc-700 dark:text-zinc-400"
          >
            ← Back to chat
          </button>
          <PersonaPanel onUse={applyPersona} />
        </aside>
      </div>
    </div>
  );
}
