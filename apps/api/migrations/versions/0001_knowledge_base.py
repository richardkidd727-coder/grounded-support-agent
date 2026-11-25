"""knowledge base: documents + chunks with pgvector and full-text search

Revision ID: 0001
Revises:
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
        CREATE TABLE kb_documents (
            id           text PRIMARY KEY,
            title        text NOT NULL,
            doc_type     text NOT NULL,
            audience     text[] NOT NULL,
            version      text NOT NULL,
            source_path  text NOT NULL,
            fingerprint  text NOT NULL,          -- content + chunker + model; drives re-indexing
            chunk_count  integer NOT NULL,
            indexed_at   timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE kb_chunks (
            id            text PRIMARY KEY,      -- "<doc_id>:<ordinal>"
            doc_id        text NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
            ordinal       integer NOT NULL,
            section_path  text NOT NULL,
            content       text NOT NULL,
            audience      text[] NOT NULL,       -- hard retrieval filter (all/free/pro/enterprise)
            token_count   integer NOT NULL,
            content_hash  text NOT NULL,
            embedding     vector(384) NOT NULL,  -- bge-small-en-v1.5, L2-normalised
            -- Headings weigh more than body text in keyword ranking.
            tsv tsvector GENERATED ALWAYS AS (
                setweight(to_tsvector('english', section_path), 'A') ||
                setweight(to_tsvector('english', content), 'B')
            ) STORED,
            UNIQUE (doc_id, ordinal)
        )
    """)
    op.execute(
        "CREATE INDEX kb_chunks_embedding_hnsw ON kb_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("CREATE INDEX kb_chunks_tsv_gin ON kb_chunks USING gin (tsv)")
    op.execute("CREATE INDEX kb_chunks_audience_gin ON kb_chunks USING gin (audience)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kb_chunks")
    op.execute("DROP TABLE IF EXISTS kb_documents")
