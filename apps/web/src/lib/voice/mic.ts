"use client";

import { Vad } from "./vad";
import { encodeWav, rms } from "./wav";

const PRE_ROLL_MS = 400; // audio kept from just before speech was detected (first syllable)

export interface MicEvents {
  onSpeechStart: () => void;
  /** Called periodically while the customer is still talking, for live captions. */
  onPartial: (wav: ArrayBuffer) => void;
  onUtterance: (wav: ArrayBuffer, durationMs: number) => void;
}

/**
 * Microphone -> AudioWorklet (raw PCM) -> VAD -> utterances as 16 kHz WAV.
 *
 * Echo cancellation, noise suppression and auto gain are requested from the browser so the
 * agent's own voice is mostly removed before VAD sees it (needed for barge-in).
 */
export class MicCapture {
  readonly vad = new Vad();
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private analyser: AnalyserNode | null = null;
  private preRoll: Float32Array[] = [];
  private utterance: Float32Array[] | null = null;
  private lastPartialAt = 0;
  private muted = false;

  constructor(
    private readonly events: MicEvents,
    private readonly partialEveryMs = 1_500,
  ) {}

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule("/pcm-capture-worklet.js");
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.node = new AudioWorkletNode(this.ctx, "pcm-capture");
    this.node.port.onmessage = (e: MessageEvent<Float32Array>) => this.onFrame(e.data);
    source.connect(this.analyser);
    source.connect(this.node);
    // The worklet must be pulled by the graph to run; route it to a muted output.
    const sink = this.ctx.createGain();
    sink.gain.value = 0;
    this.node.connect(sink).connect(this.ctx.destination);
  }

  /** Time-domain samples for the waveform (null when stopped). */
  levels(buffer: Uint8Array<ArrayBuffer>): boolean {
    if (!this.analyser) return false;
    this.analyser.getByteTimeDomainData(buffer);
    return true;
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    if (muted) this.utterance = null;
  }

  /** While the agent speaks, only clearly louder speech counts (see Vad strict mode). */
  setAgentSpeaking(speaking: boolean): void {
    this.vad.strict = speaking;
  }

  private onFrame(frame: Float32Array): void {
    if (!this.ctx || this.muted) return;
    const frameMs = (frame.length / this.ctx.sampleRate) * 1000;
    const event = this.vad.push(rms(frame), frameMs);

    if (this.utterance) {
      this.utterance.push(frame);
    } else {
      this.preRoll.push(frame);
      while (this.preRoll.length * frameMs > PRE_ROLL_MS) this.preRoll.shift();
    }

    if (event === "speech-start") {
      this.utterance = [...this.preRoll];
      this.preRoll = [];
      this.lastPartialAt = performance.now();
      this.events.onSpeechStart();
    } else if (event === "speech-end" && this.utterance) {
      const frames = this.utterance;
      this.utterance = null;
      const durationMs = frames.length * frameMs;
      this.events.onUtterance(encodeWav(frames, this.ctx.sampleRate), durationMs);
    } else if (this.utterance && performance.now() - this.lastPartialAt > this.partialEveryMs) {
      this.lastPartialAt = performance.now();
      this.events.onPartial(encodeWav(this.utterance, this.ctx.sampleRate));
    }
  }

  async stop(): Promise<void> {
    this.node?.port.close();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx?.close().catch(() => undefined);
    this.ctx = null;
    this.stream = null;
    this.node = null;
    this.analyser = null;
    this.utterance = null;
    this.preRoll = [];
  }
}
