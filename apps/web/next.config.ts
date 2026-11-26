import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";
// On Vercel a missing API URL would silently ship a site that calls localhost: fail the build.
if (process.env.VERCEL && !process.env.NEXT_PUBLIC_API_URL) {
  throw new Error("Set NEXT_PUBLIC_API_URL (the Render API URL) in the Vercel project settings.");
}
const apiUrl = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const apiOrigin = new URL(apiUrl).origin;

// No nonces: they force dynamic rendering, and this page is a static shell (see the Next.js CSP
// guide, "Without Nonces"). 'unsafe-inline' scripts are what Next's static hydration needs; the
// real exfiltration control is connect-src, which only allows this app and its API.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`, // AudioWorklet is 'self'
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self'", // next/font self-hosts the Geist files at build time
  "media-src 'self' blob: data:",
  `connect-src 'self' ${apiOrigin}${isDev ? " ws:" : ""}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  ...(apiUrl.startsWith("https://") ? ["upgrade-insecure-requests"] : []),
].join("; ");

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          // the microphone is for voice mode on this site only; nothing else is used
          { key: "Permissions-Policy", value: "microphone=(self), camera=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
