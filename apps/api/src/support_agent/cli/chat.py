"""Talk to the agent from the terminal - the full stack in-process, real models.

    uv run agent-chat "hi, I'd like a refund" "alice@northpeak.example, card 4242"
    uv run agent-chat --chaos "llm:rate_limit:once" "what is your refund policy?"
    uv run agent-chat --voice "..."          # voice-mode prompt (short spoken replies)
    uv run agent-chat --session sess_...     # continue a session

Each argument is one customer message, sent in order within one session. Prints the event stream
compactly: steps, guardrail verdicts, escalations, the streamed reply and per-turn metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from support_agent.agent.grounding import GroundingJudge
from support_agent.agent.orchestrator import Agent
from support_agent.agent.runner import TurnRunner
from support_agent.agent.store import SessionStore
from support_agent.config import get_settings
from support_agent.db import Database
from support_agent.events.schema import (
    AgentEvent,
    ErrorEvent,
    Escalated,
    GuardrailResult,
    StepCompleted,
    StepStarted,
    TokenDelta,
    TurnCompleted,
    TurnMetrics,
)
from support_agent.llm.factory import build_router
from support_agent.logs import configure_logging
from support_agent.rag.factory import build_embedder, build_retriever
from support_agent.tools.base import ToolDeps
from support_agent.tools.chaos import ChaosPlan
from support_agent.tools.registry import build_executor


def _print(event: AgentEvent) -> None:
    w = sys.stdout.write
    match event:
        case TokenDelta():
            w(event.text)
        case StepStarted():
            w(f"\n  > {event.label} [{event.name}] {event.input_preview or ''}\n")
        case StepCompleted():
            w(f"  < {event.name} {event.status} {event.duration_ms}ms - {event.summary}\n")
        case GuardrailResult():
            w(f"  # guardrail {event.decision} conf={event.confidence} {event.reasons}\n")
        case Escalated():
            w(f"  ! escalated {event.ticket_id} ({event.priority})\n")
        case ErrorEvent():
            w(f"  x error {event.code}: {event.message} retry_after={event.retry_after_s}\n")
        case TurnMetrics():
            tokens = sum(c.input_tokens + c.output_tokens for c in event.llm_calls)
            cost = sum(c.cost_usd_list_price for c in event.llm_calls)
            w(
                f"\n  = ttft {event.ttft_ms}ms, total {event.total_ms}ms, "
                f"{len(event.llm_calls)} llm calls, {tokens} tokens, ${cost:.5f} at list price\n"
            )
        case TurnCompleted():
            cites = ", ".join(c.section_path for c in event.citations)
            w(f"  = outcome {event.outcome}; sources: {cites or '-'}\n")
    sys.stdout.flush()


async def run(messages: list[str], *, session: str | None, chaos: str | None, voice: bool) -> None:
    settings = get_settings()
    configure_logging("WARNING", json=False)
    db = Database(settings.database_url)
    embedder = build_embedder(settings)
    tools = build_executor()
    async with httpx.AsyncClient() as http:
        agent = Agent(
            llm=build_router(settings.agent_llm_chain, settings, http),
            judge=GroundingJudge(build_router(settings.judge_llm_chain, settings, http)),
            tools=tools,
            deps=ToolDeps(db, build_retriever(settings, db, embedder), settings),
            settings=settings,
        )
        store = SessionStore(db, ttl_hours=settings.session_ttl_hours)
        runner = TurnRunner(agent, store)
        session_id = session or await store.create()
        print(f"session {session_id}")
        plan = ChaosPlan.parse(chaos, tools.names) if chaos else None
        for message in messages:
            print(f"\n\033[1mcustomer:\033[0m {message}\n\033[1mdrift:\033[0m", end="")
            events = await runner.start(
                session_id, message, mode="voice" if voice else "chat", chaos=plan
            )
            async for event in events:
                if event is not None:
                    _print(event)
        await runner.shutdown()
    await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("messages", nargs="+")
    parser.add_argument("--session")
    parser.add_argument("--chaos")
    parser.add_argument("--voice", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.messages, session=args.session, chaos=args.chaos, voice=args.voice))


if __name__ == "__main__":
    main()
