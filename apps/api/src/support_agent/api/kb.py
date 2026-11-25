"""Knowledge-base search endpoint (debugging, the observability drawer, and the eval UI)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from support_agent.config import Settings
from support_agent.domain import Tier
from support_agent.rag.retriever import HybridRetriever

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])

GateBand = Literal["reject", "uncertain", "confident"]


class KbHit(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    section_path: str
    content: str
    audience: list[str]
    similarity: float
    rrf_score: float
    vector_rank: int | None
    keyword_rank: int | None


class KbSearchResponse(BaseModel):
    query: str
    tier: Tier | None
    top_similarity: float
    gate: GateBand
    embed_ms: float
    search_ms: float
    hits: list[KbHit]


def gate_band(similarity: float, settings: Settings) -> GateBand:
    if similarity < settings.retrieval_reject_below:
        return "reject"
    if similarity >= settings.retrieval_accept_above:
        return "confident"
    return "uncertain"


@router.get("/search")
async def search(
    request: Request,
    q: Annotated[str, Query(min_length=2, max_length=500)],
    tier: Tier | None = None,
    k: Annotated[int, Query(ge=1, le=10)] = 5,
) -> KbSearchResponse:
    retriever: HybridRetriever = request.app.state.retriever
    settings: Settings = request.app.state.settings
    result = await retriever.search(q, tier, top_k=k)
    return KbSearchResponse(
        query=result.query,
        tier=result.tier,
        top_similarity=round(result.top_similarity, 4),
        gate=gate_band(result.top_similarity, settings),
        embed_ms=result.embed_ms,
        search_ms=result.search_ms,
        hits=[
            KbHit(
                chunk_id=c.chunk_id,
                doc_id=c.doc_id,
                title=c.title,
                section_path=c.section_path,
                content=c.content,
                audience=list(c.audience),
                similarity=round(c.similarity, 4),
                rrf_score=round(c.rrf_score, 5),
                vector_rank=c.vector_rank,
                keyword_rank=c.keyword_rank,
            )
            for c in result.chunks
        ],
    )
