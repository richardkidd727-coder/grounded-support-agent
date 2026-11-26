"""Concurrency smoke test against a running API.

    uv run e2e-server &                      # scripted models: measures *our* overhead, no quota
    uv run load-smoke --url http://127.0.0.1:8100 --sessions 25 --turns 4

Opens N conversations at once; each sends T messages back to back over SSE (the same mix as the
E2E suite: greeting, grounded policy answer, guardrail escalation, verified refund lookup).
Reports end-to-end latency percentiles and any non-200s. Point it at a real-model deployment
only with a small N - it spends model quota.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter

import httpx

MESSAGES = [
    "hello",
    "What is your refund policy?",
    "Do you offer discounts for nonprofit organisations?",
    "Which plan am I on? My email is alice@northpeak.example and my card ends in 4242.",
]


async def conversation(
    client: httpx.AsyncClient, url: str, turns: int, latencies: list[float], codes: Counter[str]
) -> None:
    res = await client.post(f"{url}/api/sessions")
    codes[f"session {res.status_code}"] += 1
    if res.status_code != 201:
        return
    sid = res.json()["session_id"]
    for i in range(turns):
        started = time.perf_counter()
        async with client.stream(
            "POST",
            f"{url}/api/chat/stream",
            json={"session_id": sid, "message": MESSAGES[i % len(MESSAGES)]},
        ) as stream:
            codes[f"turn {stream.status_code}"] += 1
            if stream.status_code != 200:
                await stream.aread()
                continue
            outcome = "none"
            async for line in stream.aiter_lines():
                if line.startswith("data: ") and '"turn.completed"' in line:
                    outcome = json.loads(line[6:])["outcome"]
            codes[f"outcome {outcome}"] += 1
        latencies.append((time.perf_counter() - started) * 1000)


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))]


async def run(url: str, sessions: int, turns: int) -> None:
    latencies: list[float] = []
    codes: Counter[str] = Counter()
    limits = httpx.Limits(max_connections=sessions * 2)
    async with httpx.AsyncClient(timeout=120, limits=limits) as client:
        started = time.perf_counter()
        await asyncio.gather(
            *(conversation(client, url, turns, latencies, codes) for _ in range(sessions))
        )
        wall = time.perf_counter() - started
    print(f"{sessions} concurrent conversations x {turns} turns in {wall:.1f}s")
    if latencies:
        print(
            f"turn latency ms: p50 {pct(latencies, 0.5):.0f} · p95 {pct(latencies, 0.95):.0f} · "
            f"max {max(latencies):.0f} · mean {statistics.mean(latencies):.0f} · "
            f"throughput {len(latencies) / wall:.1f} turns/s"
        )
    print("responses:", dict(sorted(codes.items())))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8100")
    parser.add_argument("--sessions", type=int, default=25)
    parser.add_argument("--turns", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(run(args.url.rstrip("/"), args.sessions, args.turns))


if __name__ == "__main__":
    main()
