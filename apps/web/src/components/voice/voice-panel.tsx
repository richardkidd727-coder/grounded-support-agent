"use client";

import type { Caption, VoiceState } from "@/lib/use-voice";
import type { TtsEngine } from "@/lib/voice/speaker";

import { Waveform } from "./waveform";

const STATE_LABEL: Record<VoiceState, string> = {
  off: "Voice off",
  starting: "Starting microphone…",
  listening: "Listening - go ahead",
  hearing: "Hearing you…",
  transcribing: "Transcribing…",
  thinking: "Drift is thinking…",
  speaking: "Drift is speaking - talk to interrupt",
  error: "Voice unavailable",
};

const STATE_DOT: Record<VoiceState, string> = {
  off: "bg-zinc-400",
  starting: "bg-zinc-400 animate-pulse",
  listening: "bg-emerald-500",
  hearing: "bg-emerald-500 animate-pulse",
  transcribing: "bg-amber-500 animate-pulse",
  thinking: "bg-sky-500 animate-pulse",
  speaking: "bg-sky-500",
  error: "bg-red-500",
};

export function VoicePanel({
  state,
  caption,
  error,
  engine,
  sttMs,
  micMuted,
  levels,
  onInterrupt,
  onToggleMute,
  onEnd,
}: {
  state: VoiceState;
  caption: Caption | null;
  error: string | null;
  engine: TtsEngine;
  sttMs: number | null;
  micMuted: boolean;
  levels: (buffer: Uint8Array<ArrayBuffer>) => "agent" | "customer" | null;
  onInterrupt: () => void;
  onToggleMute: () => void;
  onEnd: () => void;
}) {
  return (
    <section
      aria-label="Voice conversation"
      className="rounded-2xl border border-sky-200 bg-sky-50/60 p-3 dark:border-sky-900 dark:bg-sky-950/30"
    >
      <div className="flex items-center gap-2 text-xs">
        <span className={`size-2 rounded-full ${micMuted ? "bg-zinc-400" : STATE_DOT[state]}`} aria-hidden />
        <span role="status" aria-live="polite" className="font-medium text-zinc-800 dark:text-zinc-200">
          {micMuted ? "Microphone muted" : STATE_LABEL[state]}
        </span>
        <span className="ml-auto font-mono text-[10px] text-zinc-400">
          {engine === "server" ? "voice: Orpheus" : engine === "browser" ? "voice: browser" : "voice: text only"}
          {sttMs !== null && ` · heard in ${(sttMs / 1000).toFixed(1)}s`}
        </span>
      </div>

      <Waveform source={levels} agentSpeaking={state === "speaking"} />

      <p
        className={`min-h-5 text-sm ${caption?.final ? "text-zinc-900 dark:text-zinc-100" : "text-zinc-500 italic"}`}
        aria-live="polite"
      >
        {caption?.text ? (caption.final ? `“${caption.text}”` : `${caption.text}…`) : " "}
      </p>
      {error && <p className="text-xs text-red-600">{error}</p>}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {(state === "speaking" || state === "thinking") && (
          <button
            type="button"
            onClick={onInterrupt}
            className="rounded-full border border-sky-300 bg-white px-3 py-1 text-xs font-medium text-sky-800 hover:bg-sky-100 dark:border-sky-800 dark:bg-zinc-900 dark:text-sky-300"
          >
            Interrupt
          </button>
        )}
        <button
          type="button"
          onClick={onToggleMute}
          aria-pressed={micMuted}
          className="rounded-full border border-zinc-300 bg-white px-3 py-1 text-xs text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
        >
          {micMuted ? "Unmute" : "Mute"}
        </button>
        <button
          type="button"
          onClick={onEnd}
          className="rounded-full bg-zinc-900 px-3 py-1 text-xs font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900"
        >
          End voice
        </button>
        <span className="ml-auto text-[10px] text-zinc-400">Headphones work best. Talk over Drift to interrupt.</span>
      </div>
    </section>
  );
}
