"""Post-deploy check: is the live demo actually working, end to end?

    uv run deploy-smoke --api https://<api>.onrender.com --web https://<web>.vercel.app

Wakes a sleeping free-tier API (reporting the cold start), then checks:

* the database and models are configured (`/readyz`);
* the API and web security headers;
* CORS: the web origin is allowed and an unknown origin is not;
* the client-address hop count: a forged `X-Forwarded-For` must not change the address that the
  rate limits use, and the resolved address must be a public one (not the proxy's);
* one real conversation over SSE: a greeting, then a grounded policy answer with citations,
  which exercises the model, the embedding model, pgvector search and the grounding judge;
* the voice config and the observability summary.

Spends about three model calls of free-tier quota. Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

FORGED_IP = "203.0.113.77"  # TEST-NET-3: never a real client


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


class Smoke:
    def __init__(self, api: str, web: str | None) -> None:
        self.api = api.rstrip("/")
        self.web = web.rstrip("/") if web else None
        self.http = httpx.Client(timeout=30, follow_redirects=False)
        self.results: list[Check] = []

    def check(self, name: str, fn: Callable[[], tuple[bool, str]]) -> bool:
        try:
            ok, detail = fn()
        except Exception as exc:  # a failed check must not stop the others
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        self.results.append(Check(name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)
        return ok

    # --- checks --------------------------------------------------------------------------

    def wake(self) -> tuple[bool, str]:
        started = time.perf_counter()
        deadline = started + 180  # Render's free tier: "about one minute" to spin up
        last = ""
        while time.perf_counter() < deadline:
            try:
                res = self.http.get(f"{self.api}/healthz", timeout=20)
                if res.status_code == 200:
                    took = time.perf_counter() - started
                    state = "was asleep (cold start)" if took > 5 else "already warm"
                    return True, f"up after {took:.1f}s - {state}; version {res.json()['version']}"
                last = f"HTTP {res.status_code}"
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            time.sleep(3)
        return False, f"no healthy response within 180s (last: {last})"

    def ready(self) -> tuple[bool, str]:
        body = self.http.get(f"{self.api}/readyz").json()
        ok = body["status"] == "ready" and body["database"]
        models = ", ".join(body["llm_models"]) or "none"
        return ok, f"status {body['status']}, database {body['database']}, models: {models}"

    def api_headers(self) -> tuple[bool, str]:
        h = self.http.get(f"{self.api}/api/demo/config").headers
        want = {
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "cache-control": "no-store",
        }
        missing = [k for k, v in want.items() if h.get(k) != v]
        if "max-age" not in h.get("strict-transport-security", ""):
            missing.append("strict-transport-security (is ENV=production set?)")
        detail = f"missing/wrong: {missing}" if missing else "nosniff, DENY, no-store, HSTS"
        return not missing, detail

    def cors(self) -> tuple[bool, str]:
        assert self.web
        origin = _origin(self.web)

        def preflight(o: str) -> str | None:
            res = self.http.options(
                f"{self.api}/api/chat/stream",
                headers={
                    "Origin": o,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type,x-chaos",
                },
            )
            allowed: str | None = res.headers.get("access-control-allow-origin")
            return allowed

        allowed, foreign = preflight(origin), preflight("https://evil.example")
        ok = allowed == origin and foreign is None
        hint = "" if allowed == origin else f" - set CORS_ORIGINS={origin} on the API"
        return ok, f"{origin}: {allowed or 'blocked'}; evil.example: {foreign or 'blocked'}{hint}"

    def client_address(self) -> tuple[bool, str]:
        res = self.http.get(f"{self.api}/api/demo/client", headers={"X-Forwarded-For": FORGED_IP})
        if res.status_code == 404:
            return True, "skipped (DEMO_MODE off)"
        seen = res.json()
        chain: list[str] = seen["forwarded_for"]
        resolved = seen["client_ip"]
        try:
            public = ipaddress.ip_address(resolved).is_global
        except ValueError:
            public = False
        # Entries after the forged one were appended by the host's proxies; the first of them
        # (written by the outermost proxy) is the real client, i.e. chain[-appended].
        appended = len(chain) - chain.index(FORGED_IP) - 1 if FORGED_IP in chain else len(chain)
        if appended == 0 and seen["trusted_proxy_hops"] == 0 and resolved != FORGED_IP:
            return True, f"no proxy in front (local run): forged header ignored, peer {resolved}"
        ok = resolved != FORGED_IP and public and seen["trusted_proxy_hops"] == appended
        hint = "" if ok else f" - set TRUSTED_PROXY_HOPS={appended} (proxies appended {appended})"
        return ok, (
            f"resolved {resolved} with TRUSTED_PROXY_HOPS={seen['trusted_proxy_hops']}; "
            f"chain {chain}{hint}"
        )

    def conversation(self) -> tuple[bool, str]:
        res = self.http.post(f"{self.api}/api/sessions")
        res.raise_for_status()
        sid = res.json()["session_id"]
        notes = []
        for message, expect in (
            ("hello", None),
            ("What is your refund policy for annual plans?", "citations"),
        ):
            started = time.perf_counter()
            first_token: float | None = None
            done: dict[str, Any] | None = None
            with self.http.stream(
                "POST",
                f"{self.api}/api/chat/stream",
                json={"session_id": sid, "message": message},
                timeout=90,
            ) as stream:
                if stream.status_code != 200:
                    stream.read()
                    return False, f"{message!r}: HTTP {stream.status_code} {stream.text[:200]}"
                for line in stream.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    if event["type"] == "token" and first_token is None:
                        first_token = time.perf_counter() - started
                    elif event["type"] == "turn.completed":
                        done = event
            total = time.perf_counter() - started
            if done is None:
                return False, f"{message!r}: stream ended without turn.completed"
            citations = done.get("citations") or []
            if done["outcome"] == "degraded" or (expect == "citations" and not citations):
                return False, f"{message!r}: outcome {done['outcome']}, {len(citations)} citations"
            ttft = f"{first_token:.1f}s" if first_token is not None else "-"
            notes.append(f"{message[:24]!r} {done['outcome']} (first token {ttft}, {total:.1f}s)")
        return True, "; ".join(notes)

    def voice(self) -> tuple[bool, str]:
        body = self.http.get(f"{self.api}/api/voice/config").json()
        note = "" if body["tts"] == "server" else " - accept Orpheus terms in the Groq console"
        return bool(body["stt"]), f"speech-to-text {body['stt']}, speech output {body['tts']}{note}"

    def observability(self) -> tuple[bool, str]:
        res = self.http.get(f"{self.api}/api/observability/summary")
        return res.status_code == 200, f"HTTP {res.status_code}"

    def web_page(self) -> tuple[bool, str]:
        assert self.web
        res = self.http.get(self.web, follow_redirects=True)
        csp = res.headers.get("content-security-policy", "")
        api_origin = _origin(self.api)
        problems = []
        if res.status_code != 200:
            problems.append(f"HTTP {res.status_code}")
        if api_origin not in csp:
            problems.append(f"CSP connect-src lacks {api_origin} (NEXT_PUBLIC_API_URL at build?)")
        if res.headers.get("x-frame-options") != "DENY":
            problems.append("no X-Frame-Options")
        if "microphone=(self)" not in res.headers.get("permissions-policy", ""):
            problems.append("no Permissions-Policy")
        return not problems, "; ".join(problems) or f"200, CSP allows {api_origin}, headers set"

    # --- run -----------------------------------------------------------------------------

    def run(self, *, skip_llm: bool) -> bool:
        if not self.check("API wakes up", self.wake):
            return False
        self.check("Ready (database + models)", self.ready)
        self.check("API security headers", self.api_headers)
        self.check("Client address can't be forged", self.client_address)
        if self.web:
            self.check("CORS", self.cors)
            self.check("Web app + headers", self.web_page)
        if not skip_llm:
            self.check("Conversation (model, RAG, judge)", self.conversation)
        self.check("Voice", self.voice)
        self.check("Observability", self.observability)
        failed = [c for c in self.results if not c.ok]
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} checks passed")
        return not failed


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", required=True, help="API base URL, e.g. https://x.onrender.com")
    parser.add_argument("--web", help="web app URL, e.g. https://x.vercel.app")
    parser.add_argument("--skip-llm", action="store_true", help="don't spend model quota")
    args = parser.parse_args()
    sys.exit(0 if Smoke(args.api, args.web).run(skip_llm=args.skip_llm) else 1)


if __name__ == "__main__":
    main()
