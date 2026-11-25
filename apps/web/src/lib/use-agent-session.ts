"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { api, ApiError, type Mode, type SessionView, type TurnTrace } from "./api";
import { conversationReducer, isBusy, turnFromTrace, type TurnError } from "./conversation";

const STORAGE_KEY = "support-agent.session";
const RECOVER_POLL_MS = 1_500;
const RECOVER_GIVE_UP_MS = 60_000;
const BOOT_GIVE_UP_MS = 90_000; // free-tier API may be cold-starting

export type SessionPhase = "connecting" | "ready" | "unavailable";

function storedSessionId(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null; // private mode / blocked storage: just start fresh
  }
}

function storeSessionId(id: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // non-essential: the conversation still works, it just won't survive a reload
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Retry network failures (a sleeping free-tier host) but not HTTP errors. */
async function untilReachable<T>(fn: () => Promise<T>, isCancelled: () => boolean): Promise<T> {
  const started = Date.now();
  let delay = 1_000;
  for (;;) {
    try {
      return await fn();
    } catch (err) {
      if (err instanceof ApiError || isCancelled() || Date.now() - started > BOOT_GIVE_UP_MS) throw err;
    }
    await sleep(delay);
    delay = Math.min(delay * 2, 8_000);
  }
}

function clientError(message: string, retryable = true, retryAfterS: number | null = null): TurnError {
  return { code: "client", message, retryable, retryAfterS };
}

/**
 * One support conversation: restores the stored session on load (replaying each turn's saved
 * events), streams new turns, and recovers turns whose stream dropped - the server finishes and
 * saves them regardless, so the client polls for the saved trace instead of losing the reply.
 */
export function useAgentSession() {
  const [turns, dispatch] = useReducer(conversationReducer, []);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [phase, setPhase] = useState<SessionPhase>("connecting");
  const [customer, setCustomer] = useState<SessionView["customer"]>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const sessionRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const turnsRef = useRef(turns);
  useEffect(() => {
    turnsRef.current = turns;
  }, [turns]);

  const adopt = useCallback((id: string, traces: TurnTrace[] = []) => {
    sessionRef.current = id;
    setSessionId(id);
    storeSessionId(id);
    dispatch({ type: "reset", turns: traces.map((t) => turnFromTrace(t.turn_id, t)) });
    setPhase("ready");
  }, []);

  const refreshCustomer = useCallback(async (id: string) => {
    try {
      const view = await api.session(id);
      if (sessionRef.current === id) setCustomer(view.customer);
    } catch {
      // cosmetic; ignore
    }
  }, []);

  // Boot: resume the stored session if it is still alive, else start a new one.
  useEffect(() => {
    let cancelled = false;
    const isCancelled = () => cancelled;
    (async () => {
      const saved = storedSessionId();
      if (saved) {
        try {
          const traces = await untilReachable(() => api.turns(saved), isCancelled);
          if (cancelled) return;
          adopt(saved, traces);
          void refreshCustomer(saved);
          return;
        } catch (err) {
          if (!(err instanceof ApiError && err.status === 404)) {
            if (!cancelled) setPhase("unavailable");
            return;
          }
          // expired: fall through to a fresh session
        }
      }
      try {
        const id = await untilReachable(() => api.createSession(), isCancelled);
        if (!cancelled) adopt(id);
      } catch {
        if (!cancelled) setPhase("unavailable");
      }
    })();
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [adopt, refreshCustomer]);

  const newConversation = useCallback(async () => {
    abortRef.current?.abort();
    setNotice(null);
    setCustomer(null);
    try {
      adopt(await api.createSession());
    } catch {
      setNotice("Couldn't start a new conversation. Is the server reachable?");
    }
  }, [adopt]);

  /** Poll saved traces until the dropped turn shows up (it is saved once it completes). */
  const recover = useCallback(
    async (id: string, turnId: string | null, userText: string, known: Set<string>) => {
      const started = Date.now();
      while (Date.now() - started < RECOVER_GIVE_UP_MS && sessionRef.current === id) {
        await sleep(RECOVER_POLL_MS);
        try {
          const traces = await api.turns(id);
          const match = traces.find((t) =>
            turnId ? t.turn_id === turnId : !known.has(t.turn_id) && t.user_text === userText,
          );
          if (match) return match;
        } catch {
          // still offline; keep trying
        }
      }
      return null;
    },
    [],
  );

  const send = useCallback(
    async (text: string, opts: { mode?: Mode; chaos?: string | null } = {}) => {
      const id = sessionRef.current;
      const message = text.trim();
      if (!id || !message || isBusy(turnsRef.current.at(-1))) return;
      setNotice(null);

      const key = crypto.randomUUID();
      const known = new Set(turnsRef.current.map((t) => t.turnId).filter((t): t is string => !!t));
      dispatch({ type: "start", key, userText: message, mode: opts.mode ?? "chat" });

      const controller = new AbortController();
      abortRef.current = controller;
      let turnId: string | null = null;
      let completed = false;

      try {
        for await (const event of api.streamTurn(
          { sessionId: id, message, mode: opts.mode ?? "chat", chaos: opts.chaos },
          controller.signal,
        )) {
          if (event.type === "turn.started") turnId = event.turn_id;
          if (event.type === "turn.completed") completed = true;
          dispatch({ type: "event", key, event });
        }
        if (!completed) throw new Error("stream ended before the turn completed");
        void refreshCustomer(id);
      } catch (err) {
        if (controller.signal.aborted) return; // new conversation / unmount
        if (err instanceof ApiError) {
          const error =
            err.status === 409
              ? clientError("I'm still finishing the previous reply - try again in a moment.")
              : err.status === 404
                ? clientError("This conversation expired. Start a new one to continue.", false)
                : err.status === 429
                  ? clientError("Too many messages - please slow down.", true, err.retryAfterS)
                  : err.status >= 500
                    ? clientError("The server had a problem. Please try again.")
                    : clientError(err.message, false);
          dispatch({ type: "status", key, status: "failed", error });
          return;
        }
        // The connection dropped. The server keeps running the turn and saves it when done.
        dispatch({ type: "status", key, status: "recovering" });
        const trace = await recover(id, turnId, message, known);
        if (trace) {
          dispatch({ type: "replace", key, trace });
          void refreshCustomer(id);
        } else if (sessionRef.current === id) {
          dispatch({
            type: "status",
            key,
            status: "failed",
            error: clientError("Connection lost and the reply couldn't be recovered."),
          });
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [recover, refreshCustomer],
  );

  return {
    sessionId,
    phase,
    turns,
    customer,
    notice,
    busy: isBusy(turns.at(-1)),
    send,
    newConversation,
  };
}
