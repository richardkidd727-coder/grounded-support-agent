/** Base URL of the FastAPI backend. Inlined at build time (NEXT_PUBLIC_*). */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);
