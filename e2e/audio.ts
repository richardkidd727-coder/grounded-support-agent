/**
 * Synthetic microphone input for the voice test (Chromium's --use-file-for-fake-audio-capture).
 *
 * Speech-like tone bursts (amplitude-modulated at ~4 Hz, like syllables, so the browser's noise
 * suppression doesn't treat them as steady noise). The API's fake speech-to-text "hears" by pitch:
 * a low burst is the first configured phrase, a high burst the second.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const RATE = 48_000;

export interface Burst {
  atS: number;
  durationS: number;
  hz: number;
}

export function speechLikeWav(totalS: number, bursts: Burst[]): Buffer {
  const n = Math.round(totalS * RATE);
  const data = Buffer.alloc(44 + n * 2);
  data.write("RIFF", 0);
  data.writeUInt32LE(36 + n * 2, 4);
  data.write("WAVE", 8);
  data.write("fmt ", 12);
  data.writeUInt32LE(16, 16);
  data.writeUInt16LE(1, 20); // PCM
  data.writeUInt16LE(1, 22); // mono
  data.writeUInt32LE(RATE, 24);
  data.writeUInt32LE(RATE * 2, 28);
  data.writeUInt16LE(2, 32);
  data.writeUInt16LE(16, 34);
  data.write("data", 36);
  data.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) {
    const t = i / RATE;
    let sample = 0;
    for (const b of bursts) {
      const local = t - b.atS;
      if (local < 0 || local > b.durationS) continue;
      const fade = Math.min(1, local / 0.05, (b.durationS - local) / 0.05);
      const syllables = 0.55 + 0.45 * Math.sin(2 * Math.PI * 4 * local);
      sample += 0.6 * fade * syllables * Math.sin(2 * Math.PI * b.hz * t);
    }
    data.writeInt16LE(Math.round(Math.max(-1, Math.min(1, sample)) * 32767), 44 + i * 2);
  }
  return data;
}

export const LOW_HZ = 300; // -> "What is your refund policy?"
export const HIGH_HZ = 700; // -> "Actually, how do I export my data?"

export function writeVoiceFixture(path: string): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(
    path,
    speechLikeWav(60, [
      { atS: 2.0, durationS: 1.8, hz: LOW_HZ }, // the question
      { atS: 9.5, durationS: 1.8, hz: HIGH_HZ }, // talking over Drift's spoken answer
    ]),
  );
}
