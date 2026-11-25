"""Build RAG components from Settings (shared by the API app and the CLIs)."""

from support_agent.config import Settings
from support_agent.db import Database
from support_agent.rag.chunking import ChunkerConfig
from support_agent.rag.embeddings import Embedder, FastEmbedEmbedder
from support_agent.rag.retriever import HybridRetriever
from support_agent.rag.store import KnowledgeBaseStore


def build_embedder(
    settings: Settings, *, query_instruction: str | None = None
) -> FastEmbedEmbedder:
    return FastEmbedEmbedder(
        settings.embedding_model,
        settings.embedding_dim,
        cache_dir=settings.embedding_cache_dir,
        query_instruction=(
            settings.embedding_query_instruction if query_instruction is None else query_instruction
        ),
    )


def build_chunker_config(settings: Settings) -> ChunkerConfig:
    return ChunkerConfig(
        max_tokens=settings.chunk_max_tokens, overlap_tokens=settings.chunk_overlap_tokens
    )


def build_retriever(settings: Settings, db: Database, embedder: Embedder) -> HybridRetriever:
    return HybridRetriever(
        KnowledgeBaseStore(db), embedder, candidates=settings.retrieval_candidates
    )
