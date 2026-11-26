"use client";

import { api, ApiError } from "../api";

export type TtsEngine = "server" | "browser" | "silent";

interface Item {
  text: string;
  audio: Promise<ArrayBuffer | null> | null; // server TTS, fetched ahead of playback
}

interface Current {
  text: string;
  startedAt: number;
  expectedMs: number;
  charIndex: number; // browser engine: last word boundary reached
}

const MS_PER_CHAR = 65; // rough speaking rate, for estimating how much of a sentence was heard

function pickVoice(): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en"));
  return (
    voices.find((v) => /natural|neural|online/i.test(v.name)) ??
    voices.find((v) => /google/i.test(v.name)) ??
    voices[0] ??
    null
  );
}

/**
 * Speaks a reply sentence by sentence while it is still streaming.
 *
 * Server TTS (Groq Orpheus) audio for the next sentence is fetched while the current one plays;
 * if the server says TTS is unavailable, the browser's speech synthesis takes over seamlessly.
 * `stop()` is the barge-in path: it silences playback immediately and returns what the customer
 * actually heard, so the agent can be told where it was cut off.
 */
export class Speaker {
  private queue: Item[] = [];
  private heard: string[] = [];
  private current: Current | null = null;
  private playing = false;
  private ctx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private source: AudioBufferSourceNode | null = null;
  private abort = new AbortController();
  private finishCurrent: (() => void) | null = null;
  private idleWaiters: (() => void)[] = [];

  constructor(
    private engine: TtsEngine,
    private readonly sessionId: () => string | null,
    private readonly onSpeaking: (speaking: boolean) => void,
  ) {
    if (engine === "browser" && typeof window !== "undefined" && !("speechSynthesis" in window)) {
      this.engine = "silent";
    }
  }

  get engineName(): TtsEngine {
    return this.engine;
  }

  /** Apply the server's capability report ("browser" when Orpheus is not enabled). */
  applyEngine(engine: "server" | "browser"): void {
    this.engine = engine === "browser" && !("speechSynthesis" in window) ? "silent" : engine;
  }

  get speaking(): boolean {
    return this.playing;
  }

  /** Must be called from a user gesture (browsers block audio otherwise). */
  unlock(): void {
    if (this.ctx) return;
    this.ctx = new AudioContext();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.analyser.connect(this.ctx.destination);
    if ("speechSynthesis" in window) window.speechSynthesis.getVoices(); // warm the voice list
  }

  levels(buffer: Uint8Array<ArrayBuffer>): boolean {
    if (!this.playing || !this.analyser || this.engine !== "server") return false;
    this.analyser.getByteTimeDomainData(buffer);
    return true;
  }

  enqueue(text: string): void {
    const item: Item = { text, audio: this.engine === "server" ? this.fetchAudio(text) : null };
    this.queue.push(item);
    if (!this.playing) void this.pump();
  }

  /** Resolves once everything queued has been spoken (or stopped). */
  idle(): Promise<void> {
    if (!this.playing && this.queue.length === 0) return Promise.resolve();
    return new Promise((resolve) => this.idleWaiters.push(resolve));
  }

  /** Start a new reply: forget what was heard of the previous one. */
  reset(): void {
    this.heard = [];
  }

  /** Barge-in: silence now, drop the queue, return what the customer heard of this reply. */
  stop(): string {
    const heard = [...this.heard, this.partialOfCurrent()].filter(Boolean).join(" ");
    this.abort.abort();
    this.abort = new AbortController();
    this.queue = [];
    try {
      this.source?.stop();
    } catch {
      // already stopped
    }
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    this.finishCurrent?.();
    this.heard = [];
    return heard;
  }

  async close(): Promise<void> {
    this.stop();
    await this.ctx?.close().catch(() => undefined);
    this.ctx = null;
  }

  private partialOfCurrent(): string {
    const c = this.current;
    if (!c) return "";
    const words = c.text.split(/\s+/);
    if (c.charIndex > 0) return c.text.slice(0, c.charIndex).trim();
    const fraction = Math.min((performance.now() - c.startedAt) / Math.max(c.expectedMs, 1), 1);
    return words.slice(0, Math.round(words.length * fraction)).join(" ");
  }

  private fetchAudio(text: string): Promise<ArrayBuffer | null> {
    const id = this.sessionId();
    if (!id) return Promise.resolve(null);
    return api.speak(id, text, this.abort.signal).catch((err: unknown) => {
      if (err instanceof ApiError && err.status === 503) this.engine = "browser"; // TTS not enabled
      return null;
    });
  }

  private async pump(): Promise<void> {
    this.playing = true;
    this.onSpeaking(true);
    const signal = this.abort.signal;
    while (this.queue.length && !signal.aborted) {
      const item = this.queue.shift()!;
      const audio = item.audio ? await item.audio : null;
      if (signal.aborted) break;
      this.current = {
        text: item.text,
        startedAt: performance.now(),
        expectedMs: item.text.length * MS_PER_CHAR,
        charIndex: 0,
      };
      if (audio && this.ctx) await this.playBuffer(audio);
      else if (this.engine !== "silent") await this.playBrowser(item.text);
      if (signal.aborted) break;
      this.heard.push(item.text);
      this.current = null;
    }
    this.current = null;
    this.playing = false;
    this.onSpeaking(false);
    this.idleWaiters.splice(0).forEach((resolve) => resolve());
  }

  private async playBuffer(data: ArrayBuffer): Promise<void> {
    if (!this.ctx || !this.analyser) return;
    if (this.ctx.state === "suspended") await this.ctx.resume();
    let buffer: AudioBuffer;
    try {
      buffer = await this.ctx.decodeAudioData(data);
    } catch {
      return this.playBrowser(this.current?.text ?? "");
    }
    if (this.current) this.current.expectedMs = buffer.duration * 1000;
    await new Promise<void>((resolve) => {
      const source = this.ctx!.createBufferSource();
      source.buffer = buffer;
      source.connect(this.analyser!);
      source.onended = () => resolve();
      this.finishCurrent = resolve;
      this.source = source;
      source.start();
    });
    this.source = null;
    this.finishCurrent = null;
  }

  private playBrowser(text: string): Promise<void> {
    if (!("speechSynthesis" in window) || !text) return Promise.resolve();
    return new Promise<void>((resolve) => {
      const utterance = new SpeechSynthesisUtterance(text);
      const voice = pickVoice();
      if (voice) utterance.voice = voice;
      utterance.rate = 1.05;
      // Some browsers never fire `end` (no voices installed, headless): don't hang the queue.
      const guard = setTimeout(done, text.length * MS_PER_CHAR * 2 + 3_000);
      function done() {
        clearTimeout(guard);
        resolve();
      }
      utterance.onboundary = (e) => {
        if (this.current) this.current.charIndex = e.charIndex;
      };
      utterance.onend = done;
      utterance.onerror = done;
      this.finishCurrent = done;
      window.speechSynthesis.speak(utterance);
    }).finally(() => {
      this.finishCurrent = null;
    });
  }
}
