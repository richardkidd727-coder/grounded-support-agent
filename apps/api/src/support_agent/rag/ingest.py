"""Idempotent knowledge-base ingestion.

Each document gets a fingerprint = hash(file contents + chunker settings + embedding model). Only
documents whose fingerprint changed are re-chunked and re-embedded; documents removed from disk
are deleted from the index. Re-running ingest with nothing changed is a cheap no-op, so it is
safe to run on every deploy.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from support_agent.logs import get_logger
from support_agent.rag.chunking import ChunkerConfig, chunk_document
from support_agent.rag.documents import SourceDocument, load_documents
from support_agent.rag.embeddings import Embedder
from support_agent.rag.store import KnowledgeBaseStore

log = get_logger("rag.ingest")


@dataclass(slots=True)
class IngestReport:
    indexed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    chunks_written: int = 0
    duration_s: float = 0.0


def fingerprint(doc: SourceDocument, config: ChunkerConfig, embedder: Embedder) -> str:
    payload = f"{doc.content_hash}|{config.signature}|{embedder.model_name}:{embedder.dim}"
    return hashlib.sha256(payload.encode()).hexdigest()


async def ingest_directory(
    store: KnowledgeBaseStore,
    embedder: Embedder,
    directory: Path,
    config: ChunkerConfig | None = None,
    *,
    force: bool = False,
) -> IngestReport:
    config = config or ChunkerConfig()
    started = time.perf_counter()
    report = IngestReport()

    docs = load_documents(directory)  # validates everything before touching the index
    existing = await store.document_fingerprints()

    for doc in docs:
        fp = fingerprint(doc, config, embedder)
        if not force and existing.get(doc.doc_id) == fp:
            report.unchanged.append(doc.doc_id)
            continue
        chunks = chunk_document(doc, config)
        vectors = await embedder.embed_documents([c.embed_text for c in chunks])
        await store.replace_document(doc, fp, chunks, vectors)
        report.indexed.append(doc.doc_id)
        report.chunks_written += len(chunks)
        log.info("indexed", doc_id=doc.doc_id, chunks=len(chunks))

    stale = sorted(set(existing) - {d.doc_id for d in docs})
    await store.delete_documents(stale)
    report.deleted = stale

    report.duration_s = round(time.perf_counter() - started, 2)
    return report
