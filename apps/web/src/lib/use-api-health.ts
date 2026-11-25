"use client";

import { useEffect, useState } from "react";

import { API_URL } from "./config";

export type ApiHealth = "checking" | "waking" | "online" | "offline";

const WAKING_AFTER_MS = 2_500; // free-tier hosts sleep; a slow first ping means "cold start"
const GIVE_UP_AFTER_MS = 90_000;
const ATTEMPT_TIMEOUT_MS = 10_000;

/**
 * Pings `/healthz` on mount. Doubles as the wake-up call for a sleeping free-tier backend,
 * so the server is warm by the time the user sends a first message.
 */
export function useApiHealth(): ApiHealth {
  const [health, setHealth] = useState<ApiHealth>("checking");

  useEffect(() => {
    let cancelled = false;
    const started = Date.now();
    const wakingTimer = setTimeout(() => {
      if (!cancelled) setHealth((h) => (h === "checking" ? "waking" : h));
    }, WAKING_AFTER_MS);

    const attempt = async (delayMs: number): Promise<void> => {
      while (!cancelled && Date.now() - started < GIVE_UP_AFTER_MS) {
        try {
          const res = await fetch(`${API_URL}/healthz`, {
            cache: "no-store",
            signal: AbortSignal.timeout(ATTEMPT_TIMEOUT_MS),
          });
          if (res.ok) {
            if (!cancelled) setHealth("online");
            return;
          }
        } catch {
          // network error / timeout while the host spins up - retry below
        }
        await new Promise((r) => setTimeout(r, delayMs));
        delayMs = Math.min(delayMs * 2, 8_000);
      }
      if (!cancelled) setHealth("offline");
    };

    void attempt(1_000);
    return () => {
      cancelled = true;
      clearTimeout(wakingTimer);
    };
  }, []);

  return health;
}
