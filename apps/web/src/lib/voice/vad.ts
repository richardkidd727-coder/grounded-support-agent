/**
 * Energy-based voice activity detection with an adaptive noise floor.
 *
 * The first `calibrationMs` only learn the room's noise floor (the mic has just opened; nobody
 * is assumed to be talking yet), so a fan or street noise does not read as speech.
 *
 * Speech starts when frame energy stays above `max(minRms, floor * startRatio)` for `startMs`,
 * and ends after `endSilenceMs` below a lower (hysteresis) threshold. The noise floor tracks the
 * room only while nobody is speaking.
 *
 * "Strict" mode is used while the agent is talking: thresholds rise so the agent's own voice
 * leaking through the speakers (echo cancellation is never perfect) does not count as the
 * customer interrupting, while a customer talking over it still does.
 */

export interface VadOptions {
  minRms: number;
  startRatio: number;
  startMs: number;
  endSilenceMs: number;
  maxUtteranceMs: number;
  strictFactor: number;
  strictMinRms: number;
  strictStartMs: number;
  calibrationMs: number;
}

export const DEFAULT_VAD: VadOptions = {
  minRms: 0.012,
  startRatio: 3,
  startMs: 180,
  endSilenceMs: 850,
  maxUtteranceMs: 30_000,
  strictFactor: 3,
  strictMinRms: 0.045,
  strictStartMs: 320,
  calibrationMs: 500,
};

export type VadEvent = "speech-start" | "speech-end" | null;

export class Vad {
  floor = 0.004;
  speaking = false;
  strict = false;
  private aboveMs = 0;
  private belowMs = 0;
  private utteranceMs = 0;
  private elapsedMs = 0;

  constructor(private readonly opts: VadOptions = DEFAULT_VAD) {}

  threshold(): number {
    const base = Math.max(this.opts.minRms, this.floor * this.opts.startRatio);
    return this.strict ? Math.max(base * this.opts.strictFactor, this.opts.strictMinRms) : base;
  }

  push(level: number, frameMs: number): VadEvent {
    if (this.elapsedMs < this.opts.calibrationMs) {
      this.elapsedMs += frameMs;
      this.floor = Math.max(0.002, this.elapsedMs === frameMs ? level : this.floor * 0.7 + level * 0.3);
      return null;
    }
    const threshold = this.threshold();
    if (!this.speaking) {
      if (level > threshold) {
        this.aboveMs += frameMs;
        const needed = this.strict ? this.opts.strictStartMs : this.opts.startMs;
        if (this.aboveMs >= needed) {
          this.speaking = true;
          this.belowMs = 0;
          this.utteranceMs = this.aboveMs;
          return "speech-start";
        }
      } else {
        this.aboveMs = Math.max(0, this.aboveMs - frameMs); // tolerate brief dips
        // learn the room only while it is quiet: the agent's own (echoed) voice is not noise
        if (!this.strict) this.floor = Math.max(0.002, this.floor * 0.95 + level * 0.05);
      }
      return null;
    }

    this.utteranceMs += frameMs;
    this.belowMs = level > threshold * 0.6 ? 0 : this.belowMs + frameMs;
    if (this.belowMs >= this.opts.endSilenceMs || this.utteranceMs >= this.opts.maxUtteranceMs) {
      this.speaking = false;
      this.aboveMs = 0;
      return "speech-end";
    }
    return null;
  }
}
