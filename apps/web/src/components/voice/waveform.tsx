"use client";

import { useEffect, useRef } from "react";

type Source = (buffer: Uint8Array<ArrayBuffer>) => "agent" | "customer" | null;

const COLORS = { agent: "#0284c7", customer: "#059669", idle: "#a1a1aa" } as const;

/**
 * Live waveform of whoever is talking: the customer's microphone, or the agent's voice while it
 * speaks. Drawn on a canvas from analyser data every animation frame. When the browser speaks
 * (Web Speech API has no audio tap), a gentle synthetic wave signals "agent talking".
 */
export function Waveform({ source, agentSpeaking }: { source: Source; agentSpeaking: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const agentRef = useRef(agentSpeaking);
  useEffect(() => {
    agentRef.current = agentSpeaking;
  }, [agentSpeaking]);

  useEffect(() => {
    const el = canvas.current;
    const ctx = el?.getContext("2d");
    if (!el || !ctx) return;
    const data = new Uint8Array(new ArrayBuffer(1024));
    let frame = 0;
    let raf = 0;

    const draw = () => {
      frame += 1;
      const dpr = window.devicePixelRatio || 1;
      const width = el.clientWidth;
      const height = el.clientHeight;
      if (el.width !== width * dpr || el.height !== height * dpr) {
        el.width = width * dpr;
        el.height = height * dpr;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      let who = source(data);
      // Browser speech has no audio tap: while the agent talks, show its (synthetic) wave
      // rather than the microphone, which only picks up silence or echo.
      if (who !== "agent" && agentRef.current) {
        for (let i = 0; i < data.length; i++) {
          data[i] = 128 + Math.sin(i / 18 + frame / 6) * 34 * Math.sin(frame / 23 + i / 300);
        }
        who = "agent";
      }
      const bars = Math.max(16, Math.floor(width / 6));
      const step = Math.floor(data.length / bars);
      ctx.fillStyle = who ? COLORS[who] : COLORS.idle;
      for (let b = 0; b < bars; b++) {
        let peak = 0;
        for (let j = 0; j < step; j++) peak = Math.max(peak, Math.abs((who ? data[b * step + j] : 128) - 128));
        const h = Math.max(2, (peak / 128) * height * 1.8);
        ctx.fillRect(b * 6, (height - Math.min(h, height)) / 2, 3, Math.min(h, height));
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [source]);

  return <canvas ref={canvas} className="h-14 w-full" aria-hidden />;
}
