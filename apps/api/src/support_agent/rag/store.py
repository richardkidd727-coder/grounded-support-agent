"""Postgres/pgvector persistence for the knowledge base.

Vectors are sent as text literals and cast with `CAST(CAST(:v AS text) AS vector)`, which keeps
the driver free of pgvector-specific codecs (works the same on local Postgres and Neon).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text

from support_agent.db import Database
from support_agent.rag.chunking import Chunk
from support_agent.rag.documents import SourceDocument
from support_agent.rag.embeddings import Vector, to_pgvector

_VEC = "CAST(CAST(:qvec AS text) AS vector)"

# Lexical side: OR the query's stemmed terms (plainto_tsquery ANDs them, which is far too strict
# for natural-language questions) and rank with cover density.
# Only the constant `_VEC` cast is interpolated; every user value is a bind parameter.
_HYBRID_SQL = text(f"""
WITH q AS (
    SELECT to_tsquery('english',
             replace(plainto_tsquery('english', :qtext)::text, ' & ', ' | ')) AS tsq
),
vec AS (
    SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> {_VEC}) AS rnk
    FROM kb_chunks c
    WHERE c.audience && CAST(:audience AS text[])
    ORDER BY c.embedding <=> {_VEC}
    LIMIT :k
),
kw AS (
    SELECT c.id,
           ts_rank_cd(c.tsv, q.tsq) AS score,
           row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q.tsq) DESC) AS rnk
    FROM kb_chunks c, q
    WHERE c.tsv @@ q.tsq AND c.audience && CAST(:audience AS text[])
    ORDER BY score DESC
    LIMIT :k
)
SELECT c.id, c.doc_id, d.title, c.section_path, c.content, c.audience,
       1 - (c.embedding <=> {_VEC}) AS similarity,
       vec.rnk AS vector_rank, kw.rnk AS keyword_rank, kw.score AS keyword_score
FROM kb_chunks c
JOIN kb_documents d ON d.id = c.doc_id
LEFT JOIN vec ON vec.id = c.id
LEFT JOIN kw ON kw.id = c.id
WHERE vec.id IS NOT NULL OR kw.id IS NOT NULL
""")  # noqa: S608


@dataclass(frozen=True, slots=True)
class CandidateRow:
    chunk_id: str
    doc_id: str
    title: str
    section_path: str
    content: str
    audience: tuple[str, ...]
    similarity: float
    vector_rank: int | None
    keyword_rank: int | None
    keyword_score: float | None


@dataclass(frozen=True, slots=True)
class IndexStats:
    documents: int
    chunks: int


class KnowledgeBaseStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def document_fingerprints(self) -> dict[str, str]:
        async with self._db.engine.connect() as conn:
            rows = await conn.execute(text("SELECT id, fingerprint FROM kb_documents"))
            return {r.id: r.fingerprint for r in rows}

    async def replace_document(
        self,
        doc: SourceDocument,
        fingerprint: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Vector],
    ) -> None:
        """Atomically (re)index one document: its chunks are swapped in a single transaction."""
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch")
        async with self._db.engine.begin() as conn:
            await conn.execute(text("DELETE FROM kb_chunks WHERE doc_id = :id"), {"id": doc.doc_id})
            await conn.execute(
                text("""
                INSERT INTO kb_documents
                    (id, title, doc_type, audience, version, source_path, fingerprint,
                     chunk_count, indexed_at)
                VALUES (:id, :title, :doc_type, :audience, :version, :source_path, :fingerprint,
                        :chunk_count, now())
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title, doc_type = EXCLUDED.doc_type,
                    audience = EXCLUDED.audience, version = EXCLUDED.version,
                    source_path = EXCLUDED.source_path, fingerprint = EXCLUDED.fingerprint,
                    chunk_count = EXCLUDED.chunk_count, indexed_at = now()
                """),
                {
                    "id": doc.doc_id,
                    "title": doc.title,
                    "doc_type": doc.doc_type,
                    "audience": list(doc.audience),
                    "version": doc.version,
                    "source_path": doc.source_path,
                    "fingerprint": fingerprint,
                    "chunk_count": len(chunks),
                },
            )
            if chunks:
                await conn.execute(
                    text("""
                    INSERT INTO kb_chunks
                        (id, doc_id, ordinal, section_path, content, audience, token_count,
                         content_hash, embedding)
                    VALUES (:id, :doc_id, :ordinal, :section_path, :content, :audience,
                            :token_count, :content_hash, CAST(CAST(:embedding AS text) AS vector))
                    """),
                    [
                        {
                            "id": c.chunk_id,
                            "doc_id": c.doc_id,
                            "ordinal": c.ordinal,
                            "section_path": c.section_path,
                            "content": c.content,
                            "audience": list(c.audience),
                            "token_count": c.token_count,
                            "content_hash": c.content_hash,
                            "embedding": to_pgvector(e),
                        }
                        for c, e in zip(chunks, embeddings, strict=True)
                    ],
                )

    async def delete_documents(self, doc_ids: Sequence[str]) -> None:
        if not doc_ids:
            return
        async with self._db.engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM kb_documents WHERE id = ANY(CAST(:ids AS text[]))"),
                {"ids": list(doc_ids)},
            )

    async def hybrid_candidates(
        self, query: str, query_vector: Vector, audience: Sequence[str], k: int
    ) -> list[CandidateRow]:
        """Top-k by vector distance and top-k by keyword rank, in one round trip."""
        async with self._db.engine.connect() as conn:
            rows = await conn.execute(
                _HYBRID_SQL,
                {
                    "qtext": query,
                    "qvec": to_pgvector(query_vector),
                    "audience": list(audience),
                    "k": k,
                },
            )
            return [
                CandidateRow(
                    chunk_id=r.id,
                    doc_id=r.doc_id,
                    title=r.title,
                    section_path=r.section_path,
                    content=r.content,
                    audience=tuple(r.audience),
                    similarity=float(r.similarity),
                    vector_rank=r.vector_rank,
                    keyword_rank=r.keyword_rank,
                    keyword_score=float(r.keyword_score) if r.keyword_score is not None else None,
                )
                for r in rows
            ]

    async def stats(self) -> IndexStats:
        async with self._db.engine.connect() as c:
            docs = (await c.execute(text("SELECT count(*) FROM kb_documents"))).scalar_one()
            chunks = (await c.execute(text("SELECT count(*) FROM kb_chunks"))).scalar_one()
        return IndexStats(documents=int(docs), chunks=int(chunks))
