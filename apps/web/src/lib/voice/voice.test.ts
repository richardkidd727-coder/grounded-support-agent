import { describe, expect, it } from "vitest";

import { cleanForSpeech, SentenceStream } from "./sentences";
import { Vad } from "./vad";
import { downsample, encodeWav, rms } from "./wav";

describe("wav", () => {
  it("downsamples 48 kHz to 16 kHz by averaging", () => {
    const input = Float32Array.from([0.3, 0.3, 0.3, -0.6, -0.6, -0.6]);
    expect(Array.from(downsample(input, 48_000))).toEqual([
      expect.closeTo(0.3, 5),
      expect.closeTo(-0.6, 5),
    ]);
  });

  it("writes a valid 16-bit mono PCM header and clamps samples", () => {
    const buf = encodeWav([Float32Array.from([0, 2, -2, 0.5])], 16_000);
    const view = new DataView(buf);
    const text = (o: number, n: number) => String.fromCharCode(...new Uint8Array(buf, o, n));
    expect(text(0, 4)).toBe("RIFF");
    expect(text(8, 4)).toBe("WAVE");
    expect(view.getUint16(22, true)).toBe(1); // mono
    expect(view.getUint32(24, true)).toBe(16_000);
    expect(view.getUint32(40, true)).toBe(8); // 4 samples * 2 bytes
    expect(view.getInt16(46, true)).toBe(0x7fff); // 2 clamped to +1
    expect(view.getInt16(48, true)).toBe(-0x8000);
  });

  it("measures frame energy", () => {
    expect(rms(Float32Array.from([0.5, -0.5]))).toBeCloseTo(0.5);
  });
});

describe("Vad", () => {
  const FRAME = 40;
  const feed = (vad: Vad, level: number, ms: number) => {
    const events = [];
    for (let t = 0; t < ms; t += FRAME) events.push(vad.push(level, FRAME));
    return events.filter(Boolean);
  };

  it("detects an utterance and its end after trailing silence", () => {
    const vad = new Vad();
    expect(feed(vad, 0.003, 1000)).toEqual([]); // room noise
    expect(feed(vad, 0.08, 600)).toEqual(["speech-start"]);
    expect(feed(vad, 0.003, 400)).toEqual([]); // a pause mid-sentence is not the end
    expect(feed(vad, 0.08, 200)).toEqual([]);
    expect(feed(vad, 0.003, 1000)).toEqual(["speech-end"]);
  });

  it("ignores clicks shorter than the start window", () => {
    const vad = new Vad();
    expect(feed(vad, 0.2, 80)).toEqual([]);
    expect(feed(vad, 0.003, 500)).toEqual([]);
  });

  it("adapts to a noisy room", () => {
    const vad = new Vad();
    feed(vad, 0.03, 3000); // fan noise well above the absolute minimum
    expect(feed(vad, 0.04, 600)).toEqual([]); // slightly louder noise is not speech
    expect(feed(vad, 0.2, 600)).toEqual(["speech-start"]);
  });

  it("strict mode (agent talking) needs louder, longer speech to count as barge-in", () => {
    const vad = new Vad();
    feed(vad, 0.003, 1000);
    vad.strict = true;
    expect(feed(vad, 0.03, 1000)).toEqual([]); // echo of the agent's own voice
    expect(feed(vad, 0.15, 400)).toEqual(["speech-start"]); // customer talking over it
  });
});

describe("SentenceStream", () => {
  it("emits sentences as the reply streams, never splitting amounts or ids", () => {
    const s = new SentenceStream();
    expect(s.next("Your refund of $45")).toEqual([]);
    expect(s.next("Your refund of $45.00 was approved. It")).toEqual(["Your refund of $45.00 was approved."]);
    expect(s.next("Your refund of $45.00 was approved. It takes 5 to 10 days")).toEqual([]);
    expect(s.next("Your refund of $45.00 was approved. It takes 5 to 10 days.", true)).toEqual([
      "It takes 5 to 10 days.",
    ]);
  });

  it("merges very short fragments into the next sentence", () => {
    const s = new SentenceStream();
    expect(s.next("Sure. I can help with that refund today. ")).toEqual([
      "Sure. I can help with that refund today.",
    ]);
  });

  it("flushes the remainder on completion", () => {
    expect(new SentenceStream().next("No punctuation at the end", true)).toEqual([
      "No punctuation at the end",
    ]);
  });

  it("strips markdown before speaking", () => {
    expect(cleanForSpeech("**Ticket:** `TCK-1` see https://x.example/a\n- item")).toBe(
      "Ticket: TCK-1 see the link in the chat item",
    );
  });
});
