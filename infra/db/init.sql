-- Runs once when the local Postgres volume is first created.
-- Schema itself is managed by migrations; this only guarantees the extension exists.
CREATE EXTENSION IF NOT EXISTS vector;
