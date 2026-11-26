# Loom script (≈4½ minutes)

The order follows the rubric's weight: agentic behaviour first, then streaming and voice, error
recovery, and a short code and production tour. Timings are targets; the bold lines are what to
*show*, the quoted lines are what to *say*.

## Before you press record

* Open the live URL ~2 minutes early so Render is awake (the header badge reads **Online**).
* `curl -X POST <api>/api/demo/reset` if you've rehearsed (restores Alice's refundable invoice).
* Headphones on. Allow the microphone once in a throwaway tab, so the permission prompt doesn't
  appear on camera.
* Have these ready in the editor:
  * `apps/api/src/support_agent/crm/policy.py`
  * `apps/api/src/support_agent/agent/orchestrator.py`
  * `apps/api/src/support_agent/agent/output_guard.py`
  * `apps/api/eval/results/adversarial.md`
* Have the repo's GitHub **Actions** tab ready in another browser tab.
* Keep a second take in mind: free-tier models occasionally rate-limit. That's fine to show; the
  fallback is part of the demo.

---

### 0:00 – 0:20 · What it is

**The app, empty conversation.**

> "This is a customer support agent for a fictional SaaS called Driftwood. It streams its
> reasoning live, talks by voice, uses tools autonomously, and refuses to guess when its
> knowledge base doesn't cover a question. Everything runs on free tiers: Groq models, Neon
> Postgres with pgvector, Render and Vercel."

### 0:20 – 1:20 · The agent loop (the core)

**Demo customers → Alice → Use → send.** Let the steps stream; don't talk over the first two
seconds.

> "Alice asks for a $45 refund. The agent verifies her identity first, with her email and card
> digits, then loads her account, and asks for the refund. Notice the model never decides
> whether she gets the money: the refund tool calls a policy engine, a plain function that
> applies the plan's window, the $100 agent limit and proration, and the database has a check
> constraint so it can't over-refund even if the code were wrong."

**Click *trace* under the reply.** Point at the waterfall, time to first token, tokens, cost.

> "Every model call, tool call and guardrail check is a typed event. The same events drive the
> chat, this trace and a reload. The cost shows list price and what we actually paid on the free
> tier: zero."

### 1:20 – 1:50 · Policy limits → human hand-off

**New conversation → Bob → Use → send.**

> "Bob wants a refund on an annual plan. The prorated amount is over the agent's $100 limit, so
> the policy engine says *requires approval*, and the agent opens a ticket for the billing team
> with the plan's response time instead of promising money it can't give."

### 1:50 – 2:25 · Grounding guardrail

**Same conversation: "Do you offer discounts for nonprofit organisations?"**

> "The knowledge base doesn't cover this. Retrieval similarity is low, so the guardrail withholds
> the excerpts and the orchestrator itself opens a ticket. The model isn't trusted to escalate.
> In testing a model once *said* it had opened a ticket and invented the number; now an output
> guard catches that, too."

**Then: "What's the refund window for monthly Pro plans?"** Show the sources under the answer.

> "When the policy does cover it, it answers with citations, filtered to Bob's tier in SQL."

### 2:25 – 3:15 · Voice, with memory and an interruption

**Click 🎙 Voice.** Say: *"Which plan am I on, and when does it renew?"*

> (while it answers) "Same conversation, same memory: Bob verified by typing and isn't asked
> again. Speech goes through Whisper; the reply is spoken sentence by sentence while it's still
> streaming."

**Interrupt mid-sentence:** *"Actually, how do I export my data?"*

> "I talked over it: playback stops immediately, and the next turn knows exactly what I heard.
> You can see the *interrupted* badge, and live captions while I speak."

**Turn voice off.**

### 3:15 – 3:45 · Error recovery

**Fault menu (header) → "Model rate-limited → backup model". Send "How many seats are on our
plan?"**

> "This injects a rate limit on the primary model. The failed attempt shows in red and the backup
> model takes over without the customer noticing. Tool timeouts retry safely because refunds and
> tickets use idempotency keys. And if every model is down, the agent says so honestly and offers
> a retry with a countdown."

**Set the menu back to *No faults*.**

### 3:45 – 4:25 · Code and production readiness

**Editor:**

* `policy.py`: "rules are code";
* `orchestrator.py`, the grounding gate: "grounding is enforced, not prompted";
* `output_guard.py`.

> "The model proposes; code decides. There are tests that play a fully compromised model: it
> tries to overspend, impersonate another customer or skip verification, and nothing reaches the
> database. Against the real models, an adversarial eval ran seven injection attacks: seven held."

**GitHub Actions tab: green run.**

> "CI runs lint, strict typing, about 250 API tests, and a Playwright suite that drives the
> production build, voice included, with scripted models so it's free and deterministic. There
> are also rate limits per IP and per conversation, a content security policy, and a post-deploy
> smoke test."

### 4:25 – 4:45 · Close

> "The biggest trade-off was voice: I chose a speech-to-text, agent, speech cascade over a
> speech-to-speech model, so voice gets exactly the same tools, guardrails and memory as chat, at
> the cost of about a second of latency. The README covers the architecture and the other
> trade-offs. Thanks for watching."

---

**If something goes wrong on camera:**

* *Models are busy* with a countdown: say "that's the free tier's per-minute limit; this is the
  honest degraded path", wait for it, and retry.
* Voice not picking you up: check the mic badge, and speak a little louder; it calibrates to the
  room in the first half second.
