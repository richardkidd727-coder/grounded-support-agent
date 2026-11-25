"""Hybrid retrieval: pgvector cosine + Postgres full-text, fused with Reciprocal Rank Fusion.

Two scores come back on purpose:

* `rrf_score` orders results. It is rank-based, so it is robust to the two retrievers having
  incomparable score scales - but for the same reason it says nothing about *absolute* relevance.
* `similarity` (cosine, 0..1) is absolute and comparable across queries. The confidence guardrail
  uses `top_similarity`, with a threshold calibrated by `uv run eval-retrieval`.

The audience filter is applied inside the SQL, so content for other tiers is never even a
candidate - it cannot leak into the prompt.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from support_agent.domain import Tier, allowed_audiences
from support_agent.rag.embeddings import Embedder
from support_agent.rag.store import CandidateRow, KnowledgeBaseStore

RRF_K = 60  # standard constant from Cormack et al. (2009); damps the influence of top ranks


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    title: str
    section_path: str
    content: str
    audience: tuple[str, ...]
    similarity: float
    rrf_score: float
    vector_rank: int | None
    keyword_rank: int | None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    query: str
    tier: Tier | None
    chunks: list[RetrievedChunk]
    top_similarity: float  # best cosine similarity among all candidates (0 if none)
    embed_ms: float
    search_ms: float


def reciprocal_rank_fusion(ranks: Sequence[int | None], k: int = RRF_K) -> float:
    return sum(1.0 / (k + r) for r in ranks if r is not None)


def fuse(candidates: Sequence[CandidateRow], top_k: int, k: int = RRF_K) -> list[RetrievedChunk]:
    fused = [
        RetrievedChunk(
            chunk_id=c.chunk_id,
            doc_id=c.doc_id,
            title=c.title,
            section_path=c.section_path,
            content=c.content,
            audience=c.audience,
            similarity=c.similarity,
            rrf_score=reciprocal_rank_fusion((c.vector_rank, c.keyword_rank), k),
            vector_rank=c.vector_rank,
            keyword_rank=c.keyword_rank,
        )
        for c in candidates
    ]
    # Tie-break on similarity so equal-RRF chunks keep a stable, meaningful order.
    fused.sort(key=lambda c: (c.rrf_score, c.similarity), reverse=True)
    return fused[:top_k]


class HybridRetriever:
    def __init__(
        self, store: KnowledgeBaseStore, embedder: Embedder, *, candidates: int = 20
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._candidates = candidates

    async def search(self, query: str, tier: Tier | None, top_k: int = 5) -> RetrievalResult:
        t0 = time.perf_counter()
        query_vector = await self._embedder.embed_query(query)
        t1 = time.perf_counter()
        candidates = await self._store.hybrid_candidates(
            query, query_vector, allowed_audiences(tier), self._candidates
        )
        t2 = time.perf_counter()
        return RetrievalResult(
            query=query,
            tier=tier,
            chunks=fuse(candidates, top_k),
            top_similarity=max((c.similarity for c in candidates), default=0.0),
            embed_ms=round((t1 - t0) * 1000, 1),
            search_ms=round((t2 - t1) * 1000, 1),
        )
