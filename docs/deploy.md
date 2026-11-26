# Deploying: Neon + Render + Vercel

About 20 minutes, all on free plans with no card. Order matters: database → API → web → wire
CORS back to the web URL → verify.

You need the GitHub repo, a Groq API key (`GROQ_API_KEY` from your `.env`), and the three
accounts below (sign up with GitHub for each).

## 1. Database: Neon

1. <https://neon.com> → **Sign up** → **Create project**:
   * Name: `support-agent`
   * Postgres version: **17**
   * Region: **AWS US West 2 (Oregon)**. It sits next to the Render region in `render.yaml`;
     if you change one, change both.
2. On the project dashboard click **Connect**. Turn **Connection pooling off** to get the
   *direct* string; the pooled one also works, but direct is simpler for a single server. Copy
   the string. It looks like:

   ```
   postgresql://neondb_owner:...@ep-xxx-yyy.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   ```

   Nothing else to do. The API's first boot enables `pgvector`, creates the tables, indexes the
   knowledge base and seeds the demo customers.

## 2. API: Render

1. <https://render.com> → **Sign up** with GitHub → allow access to the repository.
2. **New → Blueprint** → pick the repo. Render reads [`render.yaml`](../render.yaml) and shows
   one service, `support-agent-api` (free, Docker).
3. Fill in the three prompted values:

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | the Neon string from step 1 |
   | `GROQ_API_KEY` | your Groq key |
   | `CORS_ORIGINS` | `http://localhost:3000` for now; replaced in step 4 |

4. **Apply**. The first build takes ~5–10 minutes: it installs dependencies and bakes the
   embedding model into the image. The *first boot* then embeds the knowledge base into the
   empty database, which takes up to ~6 minutes on the free CPU, once. The log shows `indexed …`
   lines, `boot: seeded 9 demo customers`, `embedder_ready`, then a JSON `startup` line. Later
   boots skip the indexing and take about a minute.
5. Copy the service URL, e.g. `https://support-agent-api-xxxx.onrender.com`. Open
   `<url>/readyz`; it should say `"status":"ready","database":true`.

## 3. Web: Vercel

1. <https://vercel.com> → **Sign up** with GitHub → **Add New… → Project** → import the repo.
2. **Root Directory → Edit → `apps/web`**. Vercel detects Next.js and the npm workspace; leave
   the build and install commands at their defaults.
3. **Environment Variables**: `NEXT_PUBLIC_API_URL` = the Render URL from step 2, with no
   trailing slash. The build fails on purpose if this is missing: the URL is compiled into the
   page and its security policy.
4. **Deploy**. Copy the production URL, e.g. `https://grounded-support-agent.vercel.app`.

If you change `NEXT_PUBLIC_API_URL` later, **Redeploy**: it is read at build time.

## 4. Point the API at the web app

Render → `support-agent-api` → **Environment** → set `CORS_ORIGINS` to the Vercel production URL
(no trailing slash) → **Save, rebuild and deploy**. Several origins are comma-separated. Vercel
*preview* URLs are not allowed on purpose; only production is.

## 5. Groq: enable the server voice (one time)

Open <https://console.groq.com/playground?model=canopylabs/orpheus-v1-english> and accept the
model's terms. Until then voice replies use the browser's own speech synthesis. Everything else
works either way. The API re-checks on its next restart.

## 6. Verify

From the repo:

```bash
cd apps/api
uv run deploy-smoke --api https://support-agent-api-xxxx.onrender.com --web https://<your-app>.vercel.app
```

Every line should say `PASS`. The check spends about three model calls. If the
*Client address* line fails, it prints the `TRUSTED_PROXY_HOPS` value to set on Render.

Then open the web URL and run through *Try it in a minute* in the README. The mic needs HTTPS,
which both hosts provide.

## Optional: keep the demo warm while it's being reviewed

Render's free instance sleeps after 15 idle minutes and takes ~1 minute to wake. To reduce that:

* GitHub repo → **Settings → Secrets and variables → Actions → Variables → New repository
  variable**.
* Name it `KEEP_WARM_URL`, with the value `https://support-agent-api-xxxx.onrender.com/healthz`.

[`keep-warm.yml`](../.github/workflows/keep-warm.yml) then pings it every 10 minutes. One
always-on service uses ~744 of the 750 free instance hours a month. Delete the variable to stop.

## Before recording the demo

* Open the web URL a minute early, so the API is awake.
* Reset the demo customers if you've already used them:
  `curl -X POST https://support-agent-api-xxxx.onrender.com/api/demo/reset`. Every restart also
  re-seeds them.
* Use headphones for voice, so the agent's voice doesn't trigger an interruption.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Page stuck on *Waking server*, then *Server unreachable* | The API is still building or crashed: Render → Logs. A wrong `NEXT_PUBLIC_API_URL` looks the same; check it and redeploy Vercel |
| Browser console: *blocked by CORS policy* | `CORS_ORIGINS` on Render doesn't exactly match the Vercel URL (scheme, no trailing slash) |
| Browser console: *violates Content Security Policy … connect-src* | Vercel was built with a different API URL: fix `NEXT_PUBLIC_API_URL`, redeploy |
| `/readyz` shows `"database": false` | `DATABASE_URL` is wrong, or the Neon compute is suspended and still waking: retry |
| Replies say the models are busy | Groq free-tier per-minute limits; wait for the countdown. `GROQ_API_KEY` missing shows as `"status":"degraded"` in `/readyz` |
| Render deploy didn't start after a push | `autoDeployTrigger: checksPass` waits for CI to go green on `main`; a change outside `apps/api/` doesn't redeploy the API |
| Memory errors in Render logs | The free instance has 512 MB; measured peak use is ~285 MB (see the README), so look at recent changes |
| First deploy marked failed while `indexed …` lines are still appearing | The first boot is still embedding the knowledge base. Redeploy: indexing resumes, and already-indexed documents are skipped |
