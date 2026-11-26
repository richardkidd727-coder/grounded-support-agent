import { resolve } from "node:path";

import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end: the real API (FastAPI + Postgres + tools + guardrails + RAG with real embeddings)
 * and the production web build, with scripted models and fake speech so runs are deterministic,
 * offline and free. `uv run e2e-server` uses its own `support_e2e` database.
 */
export const API_URL = "http://127.0.0.1:8100";
export const WEB_URL = "http://localhost:3100";
export const VOICE_WAV = resolve(__dirname, ".artifacts/voice.wav");

const ci = !!process.env.CI;

export default defineConfig({
  testDir: "./tests",
  globalSetup: "./global-setup.ts",
  fullyParallel: false,
  workers: 1, // one shared database and rate limiter; the suite is short
  retries: ci ? 1 : 0,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: ci ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: WEB_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // CI installs Chromium; locally, use the installed Edge (no browser download).
    channel: ci ? undefined : "msedge",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], channel: ci ? undefined : "msedge" } },
  ],
  webServer: [
    {
      command: `uv run --directory ../apps/api e2e-server --port 8100 --web-origin ${WEB_URL}`,
      url: `${API_URL}/readyz`,
      timeout: 300_000, // first run downloads the embedding model and indexes the knowledge base
      reuseExistingServer: !ci,
      stdout: ci ? "pipe" : "ignore", // API logs help debug CI failures; noise locally
    },
    {
      command: "npm run build -w web && npm run start -w web -- -p 3100",
      cwd: "..",
      env: { NEXT_PUBLIC_API_URL: API_URL },
      url: WEB_URL,
      timeout: 300_000,
      reuseExistingServer: !ci,
    },
  ],
});
