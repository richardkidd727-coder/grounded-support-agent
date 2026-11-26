"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import type { TurnView } from "./conversation";
import type { SendOptions } from "./use-agent-session";
import { MicCapture } from "./voice/mic";
import { SentenceStream } from "./voice/sentences";
import { Speaker, type TtsEngine } from "./voice/speaker";

export type VoiceState =
  | "off"
  | "starting"
  | "listening" // mic open, waiting for speech
  | "hearing" // customer is talking
  | "transcribing"
  | "thinking" // agent turn running, nothing spoken yet
  | "speaking"
  | "error";

export interface Caption {
  text: string;
  final: boolean;
}

const MIN_UTTERANCE_MS = 350; // coughs and clicks

interface Args {
  sessionId: string | null;
  turns: TurnView[];
  send: (text: string, opts: SendOptions) => Promise<void>;
  whenIdle: () => Promise<void>;
  chaos: string | null;
}

/**
 * Voice conversation controller:
 *
 *   mic --VAD--> utterance --Whisper--> text --/api/chat/stream (mode=voice)--> reply tokens
 *     --sentences--> TTS queue --> speaker
 *
 * Barge-in: speech detected while the agent is thinking or speaking stops playback immediately,
 * mutes the rest of that reply, and the next turn tells the agent what the customer heard.
 * The agent turn itself is not cancelled - it may be mid-refund - it finishes in the transcript.
 */
export function useVoice({ sessionId, turns, send, whenIdle, chaos }: Args) {
  const [state, setState] = useState<VoiceState>("off");
  const [caption, setCaption] = useState<Caption | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [engine, setEngine] = useState<TtsEngine>("server");
  const [sttMs, setSttMs] = useState<number | null>(null);
  const [micMuted, setMicMuted] = useState(false);

  const stateRef = useRef<VoiceState>("off");
  const micRef = useRef<MicCapture | null>(null);
  const speakerRef = useRef<Speaker | null>(null);
  const activeKey = useRef<string | null>(null);
  const sentences = useRef(new SentenceStream());
  const mutedKeys = useRef(new Set<string>());
  const pendingInterruption = useRef<string | null>(null);
  // Transcriptions of utterances not yet sent. People pause mid-thought; if they resume before
  // we reply, the pieces are joined - in speaking order, whatever order Whisper answers in.
  const segments = useRef<{ seq: number; text: Promise<string>; audioMs: number }[]>([]);
  const sttModel = useRef("whisper");
  const lastSttMs = useRef(0);
  const utteranceSeq = useRef(0);
  const partialInFlight = useRef(false);
  const live = useRef({ sessionId, chaos, send, whenIdle });
  useEffect(() => {
    live.current = { sessionId, chaos, send, whenIdle };
  }, [sessionId, chaos, send, whenIdle]);

  const go = useCallback((next: VoiceState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  /** Stop the agent talking; remember what was heard for the next turn. */
  const bargeIn = useCallback(() => {
    const was = stateRef.current;
    if (was !== "speaking" && was !== "thinking") return;
    const heard = speakerRef.current?.stop() ?? "";
    const key = activeKey.current;
    if (key) mutedKeys.current.add(key); // reply still streaming: don't speak the rest of it
    pendingInterruption.current = was === "speaking" ? heard : "";
  }, []);

  const onSpeechStart = useCallback(() => {
    bargeIn();
    utteranceSeq.current += 1;
    setCaption({ text: "", final: false });
    go("hearing");
  }, [bargeIn, go]);

  const onPartial = useCallback((wav: ArrayBuffer) => {
    const id = live.current.sessionId;
    // Captions are best-effort: skip them while a final transcription is still in flight.
    if (!id || partialInFlight.current || segments.current.length > 0) return;
    partialInFlight.current = true;
    const seq = utteranceSeq.current;
    api
      .transcribe(id, wav)
      .then((t) => {
        if (seq === utteranceSeq.current && stateRef.current === "hearing" && t.text) {
          setCaption({ text: t.text, final: false });
        }
      })
      .catch(() => undefined) // captions are best-effort
      .finally(() => {
        partialInFlight.current = false;
      });
  }, []);

  const onUtterance = useCallback(
    async (wav: ArrayBuffer, durationMs: number) => {
      const id = live.current.sessionId;
      if (!id) return;
      const seq = utteranceSeq.current;
      let failed = false;
      const text =
        durationMs < MIN_UTTERANCE_MS
          ? Promise.resolve("") // a cough or click: still a segment, so nothing is stranded
          : api
              .transcribe(id, wav)
              .then((r) => {
                setSttMs(r.latency_ms);
                lastSttMs.current = r.latency_ms;
                return r.text;
              })
              .catch(() => {
                failed = true;
                return "";
              });
      segments.current.push({ seq, text, audioMs: durationMs });
      go("transcribing");
      await text;
      // Only the newest utterance sends; it collects the earlier ones.
      if (seq !== utteranceSeq.current) return;
      const parts = segments.current;
      segments.current = [];
      const joined = (await Promise.all(parts.map((p) => p.text))).join(" ").replace(/\s+/g, " ").trim();
      if (seq !== utteranceSeq.current) {
        segments.current = [...parts, ...segments.current]; // they spoke again meanwhile
        return;
      }
      if (!joined) {
        setCaption(null);
        if (failed) setError("Sorry, I didn't catch that - please try again.");
        go("listening");
        return;
      }
      const message = joined;
      setError(null);
      setCaption({ text: message, final: true });

      await live.current.whenIdle(); // an interrupted turn may still be finishing on the server
      const key = crypto.randomUUID();
      activeKey.current = key;
      sentences.current = new SentenceStream();
      speakerRef.current?.reset();
      go("thinking");
      const interruptedAfter = pendingInterruption.current;
      pendingInterruption.current = null;
      await live.current.send(message, {
        mode: "voice",
        key,
        chaos: live.current.chaos,
        interruptedAfter,
        voiceInput: {
          stt_model: sttModel.current,
          stt_ms: lastSttMs.current, // the final utterance's round trip: what the customer waited
          audio_s: Math.round(parts.reduce((n, p) => n + p.audioMs, 0) / 100) / 10,
        },
      });
    },
    [go],
  );

  // Speak the active turn's reply as it streams in.
  useEffect(() => {
    const key = activeKey.current;
    const speaker = speakerRef.current;
    if (!key || !speaker) return;
    const turn = turns.find((t) => t.key === key);
    if (!turn) return;
    const final = turn.status === "done" || turn.status === "failed";
    if (!mutedKeys.current.has(key)) {
      const text = turn.text || (turn.status === "failed" ? (turn.error?.message ?? "") : "");
      for (const sentence of sentences.current.next(text, final)) speaker.enqueue(sentence);
    }
    if (final) {
      activeKey.current = null;
      void speaker.idle().then(() => {
        if (stateRef.current === "thinking" || stateRef.current === "speaking") go("listening");
      });
    }
  }, [turns, go]);

  const start = useCallback(async () => {
    if (stateRef.current !== "off" && stateRef.current !== "error") return;
    setError(null);
    go("starting");
    // Create audio output inside the click handler: browsers only allow it from a user gesture.
    const speaker = new Speaker("server", () => live.current.sessionId, (speaking) => {
      micRef.current?.setAgentSpeaking(speaking);
      if (speaking && stateRef.current === "thinking") go("speaking");
      setEngine(speakerRef.current?.engineName ?? "server");
    });
    speaker.unlock();
    speakerRef.current = speaker;

    const config = await api.voiceConfig().catch(() => null);
    if (!config?.stt) {
      setError("Voice isn't available on this server.");
      go("error");
      return;
    }
    speaker.applyEngine(config.tts);
    sttModel.current = config.stt_model;
    setEngine(speaker.engineName);

    const mic = new MicCapture({
      onSpeechStart,
      onPartial,
      onUtterance: (wav, ms) => void onUtterance(wav, ms),
    });
    try {
      await mic.start();
    } catch (err) {
      await mic.stop();
      setError(
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "Microphone access was blocked. Allow it in the browser to use voice."
          : "Couldn't open a microphone.",
      );
      go("error");
      return;
    }
    micRef.current = mic;
    setMicMuted(false);
    go("listening");
  }, [go, onPartial, onSpeechStart, onUtterance]);

  const stop = useCallback(async () => {
    const mic = micRef.current;
    const speaker = speakerRef.current;
    micRef.current = null;
    speakerRef.current = null;
    activeKey.current = null;
    segments.current = [];
    pendingInterruption.current = null;
    setCaption(null);
    go("off");
    await Promise.all([mic?.stop(), speaker?.close()]);
  }, [go]);

  const toggleMute = useCallback(() => {
    setMicMuted((m) => {
      micRef.current?.setMuted(!m);
      return !m;
    });
  }, []);

  /** Time-domain samples of whoever is talking (agent output while speaking, else the mic). */
  const levels = useCallback((buffer: Uint8Array<ArrayBuffer>): "agent" | "customer" | null => {
    if (speakerRef.current?.levels(buffer)) return "agent";
    if (micRef.current?.levels(buffer)) return "customer";
    return null;
  }, []);

  useEffect(
    () => () => {
      void micRef.current?.stop();
      void speakerRef.current?.close();
    },
    [],
  );

  return {
    state,
    caption,
    error,
    engine,
    sttMs,
    micMuted,
    active: state !== "off" && state !== "error",
    start,
    stop,
    interrupt: bargeIn,
    toggleMute,
    levels,
  };
}
