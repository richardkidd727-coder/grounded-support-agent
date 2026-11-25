"""Evaluate retrieval and calibrate the similarity threshold.

Usage: uv run eval-retrieval [--no-instruction] [--out PATH]
Requires an ingested index (uv run ingest).
"""

import argparse
import asyncio
from pathlib import Path

from support_agent.config import API_ROOT, get_settings
from support_agent.db import Database
from support_agent.logs import configure_logging
from support_agent.rag.evaluation import EvalSummary, load_cases, render_markdown, run_eval
from support_agent.rag.factory import build_chunker_config, build_embedder, build_retriever

DEFAULT_CASES = API_ROOT / "eval" / "retrieval_cases.yaml"
DEFAULT_OUT = API_ROOT / "eval" / "results" / "retrieval.md"


async def _evaluate(no_instruction: bool) -> tuple[EvalSummary, list[str]]:
    settings = get_settings()
    db = Database(settings.database_url)
    embedder = build_embedder(settings, query_instruction="" if no_instruction else None)
    try:
        summary = await run_eval(
            build_retriever(settings, db, embedder),
            load_cases(DEFAULT_CASES),
            top_k=settings.retrieval_top_k,
        )
    finally:
        await db.dispose()

    config_lines = [
        f"Embedding model: `{settings.embedding_model}`",
        f"Query instruction: {'off' if no_instruction else 'on'}",
        f"Chunker: `{build_chunker_config(settings).signature}`",
        f"Retrieval: hybrid (pgvector cosine + Postgres FTS), RRF, "
        f"{settings.retrieval_candidates} candidates per retriever, top {settings.retrieval_top_k}",
    ]
    return summary, config_lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-instruction", action="store_true", help="embed queries as-is")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    configure_logging("WARNING")

    summary, config_lines = asyncio.run(_evaluate(args.no_instruction))
    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(summary, config_lines), encoding="utf-8")

    b = summary.bands
    print(
        f"hit@1 {summary.hit_at[1]:.0%}  hit@3 {summary.hit_at[3]:.0%}  "
        f"hit@5 {summary.hit_at[5]:.0%}  MRR {summary.mrr:.3f}  |  gate: reject <"
        f"{b.reject_below:.2f}, confident >={b.accept_above:.2f}; judge sees "
        f"{b.in_scope_to_judge:.0%} of answerable, {b.out_of_scope_to_judge:.0%} of out-of-scope"
        f"  ->  {out}"
    )


if __name__ == "__main__":
    main()
