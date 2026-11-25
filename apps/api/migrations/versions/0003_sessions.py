"""conversation sessions, messages and per-turn traces

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No FK from sessions to customers on purpose: re-seeding the demo CRM (TRUNCATE ... CASCADE)
    # must not wipe conversation history, and a session may never verify at all.
    op.execute("""
        CREATE TABLE chat_sessions (
            id          text PRIMARY KEY,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            auth        jsonb NOT NULL DEFAULT '{}'::jsonb,  -- verified customer, tier, attempts
            turn_count  integer NOT NULL DEFAULT 0
        )
    """)
    op.execute("""
        CREATE TABLE chat_messages (
            id            bigserial PRIMARY KEY,
            session_id    text NOT NULL REFERENCES chat_sessions (id) ON DELETE CASCADE,
            turn_id       text NOT NULL,
            mode          text NOT NULL CHECK (mode IN ('chat', 'voice')),
            role          text NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
            content       text NOT NULL DEFAULT '',
            tool_calls    jsonb,
            tool_call_id  text,
            tool_name     text,
            created_at    timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX chat_messages_session_idx ON chat_messages (session_id, id)")
    op.execute("""
        CREATE TABLE agent_turns (
            id                  text PRIMARY KEY,
            session_id          text NOT NULL REFERENCES chat_sessions (id) ON DELETE CASCADE,
            mode                text NOT NULL CHECK (mode IN ('chat', 'voice')),
            user_text           text NOT NULL,
            final_text          text NOT NULL,
            outcome             text NOT NULL
                                CHECK (outcome IN ('answered', 'escalated', 'degraded')),
            started_at          timestamptz NOT NULL,
            total_ms            integer NOT NULL,
            ttft_ms             integer,
            llm_calls           integer NOT NULL,
            input_tokens        integer NOT NULL,
            output_tokens       integer NOT NULL,
            cost_usd_list_price numeric(12, 8) NOT NULL,
            events              jsonb NOT NULL  -- the full ordered event stream (observability)
        )
    """)
    op.execute("CREATE INDEX agent_turns_session_idx ON agent_turns (session_id, started_at)")


def downgrade() -> None:
    op.execute("DROP TABLE agent_turns")
    op.execute("DROP TABLE chat_messages")
    op.execute("DROP TABLE chat_sessions")
