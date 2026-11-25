"""Postgres + pgvector integration: ingest the real corpus, then retrieve through SQL."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from support_agent.config import API_ROOT, Settings
from support_agent.db import Database
from support_agent.domain import Tier, allowed_audiences
from support_agent.main import create_app
from support_agent.rag.embeddings import HashingEmbedder
from support_agent.rag.ingest import ingest_directory
from support_agent.rag.retriever import HybridRetriever
from support_agent.rag.store import KnowledgeBaseStore

pytestmark = pytest.mark.integration

CORPUS = API_ROOT / "knowledge_base"
TIER_PROBES = [
    "What is the maximum refund support can issue?",
    "How fast do you respond to urgent problems?",
    "Can we store data in the EU?",
    "contract expired grace period",
]


async def _ingest(db: Database, directory: Path = CORPUS) -> KnowledgeBaseStore:
    store = KnowledgeBaseStore(db)
    await ingest_directory(store, HashingEmbedder(), directory)
    return store


async def test_ingest_is_idempotent_and_tracks_deletions(pg_db: Database, tmp_path: Path) -> None:
    store = KnowledgeBaseStore(pg_db)
    embedder = HashingEmbedder()

    first = await ingest_directory(store, embedder, CORPUS)
    assert len(first.indexed) >= 12
    stats = await store.stats()
    assert stats.chunks == first.chunks_written

    second = await ingest_directory(store, embedder, CORPUS)
    assert second.indexed == [] and len(second.unchanged) == len(first.indexed)

    # Remove one document and edit another: only those two change.
    corpus = tmp_path / "kb"
    shutil.copytree(CORPUS, corpus)
    (corpus / "service-credits.md").unlink()
    edited = corpus / "troubleshooting-api.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\nOne more line.\n", encoding="utf-8")

    third = await ingest_directory(store, embedder, corpus)
    assert third.deleted == ["service-credits"]
    assert third.indexed == ["troubleshooting-api"]
    assert (await store.stats()).documents == len(first.indexed) - 1


@pytest.mark.parametrize("tier", [None, Tier.FREE, Tier.PRO, Tier.ENTERPRISE])
async def test_retrieval_never_returns_content_for_other_tiers(
    pg_db: Database, tier: Tier | None
) -> None:
    retriever = HybridRetriever(await _ingest(pg_db), HashingEmbedder(), candidates=50)
    allowed = set(allowed_audiences(tier))
    for probe in TIER_PROBES:
        result = await retriever.search(probe, tier, top_k=50)
        assert result.chunks, probe
        for chunk in result.chunks:
            assert set(chunk.audience) & allowed, (tier, probe, chunk.section_path)


async def test_hybrid_search_finds_the_right_section(pg_db: Database) -> None:
    retriever = HybridRetriever(await _ingest(pg_db), HashingEmbedder())
    result = await retriever.search("API returns 429 too many requests", Tier.PRO)
    assert result.chunks[0].section_path.endswith("429 Too Many Requests")
    assert result.chunks[0].keyword_rank is not None  # lexical side contributed
    assert 0 < result.top_similarity <= 1


async def test_kb_search_endpoint(pg_db: Database, test_database_url: str) -> None:
    await _ingest(pg_db)
    settings = Settings(env="test", database_url=test_database_url, _env_file=None)  # type: ignore[call-arg]
    with TestClient(create_app(settings, embedder=HashingEmbedder())) as client:
        res = client.get("/api/kb/search", params={"q": "forgot my password", "k": 3})
        assert res.status_code == 200
        body = res.json()
        assert body["gate"] in {"reject", "uncertain", "confident"}
        assert len(body["hits"]) == 3
        assert body["hits"][0]["doc_id"] == "troubleshooting-login"

        assert client.get("/api/kb/search", params={"q": "x"}).status_code == 422
        assert client.get("/api/kb/search", params={"q": "ok", "tier": "vip"}).status_code == 422
