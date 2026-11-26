"""Application settings, loaded from environment variables (and `.env` in local dev)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[2]  # apps/api (or /app in the container)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Works whether the API is started from apps/api or from the repo root.
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- runtime -----------------------------------------------------------
    env: Literal["local", "test", "production"] = "local"
    log_level: str = "INFO"
    cors_origins: str = Field(
        default="http://localhost:3000",
        description="Comma-separated list of allowed browser origins.",
    )

    # --- storage -----------------------------------------------------------
    database_url: str = "postgresql://support:support@localhost:55432/support"

    # --- LLM providers (free tiers) ------------------------------------------
    # Model ids are config, not code: confirm the current free models in the Groq console / AI
    # Studio. A chain is an ordered fallback list of `provider:model`; each model has its own
    # free-tier quota, so falling back to the next one also spreads load across buckets.
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    # gpt-oss-20b is judge-only: as an agent it was seen inventing a ticket id instead of calling
    # the tool. Small models are fine for narrow JSON verdicts, not for multi-step tool use.
    agent_llm_chain: str = "groq:openai/gpt-oss-120b,groq:qwen/qwen3.8-27b"
    judge_llm_chain: str = "groq:openai/gpt-oss-20b,groq:qwen/qwen3.8-27b"
    llm_read_timeout_s: float = 20.0  # max silence between streamed chunks (incl. first token)
    llm_total_timeout_s: float = 45.0  # one model call, end to end
    # Wait up to this long for a rate-limited model to free up, else fail fast with Retry-After.
    # Generous because free-tier per-minute buckets refill within seconds.
    llm_max_cooldown_wait_s: float = 12.0
    # Free tiers bill nothing; metrics still report cost at list price for capacity planning.
    llm_billing: Literal["free", "paid"] = "free"

    # --- voice (Groq audio; same key) ---------------------------------------------
    groq_stt_model: str = "whisper-large-v3-turbo"
    # Orpheus needs a one-time terms acceptance in the Groq console; until then the browser's
    # own speech synthesis is used automatically.
    groq_tts_model: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice: str = "troy"
    voice_max_audio_bytes: int = 4_000_000  # ~2 minutes of 16 kHz mono WAV

    # --- agent -------------------------------------------------------------------
    agent_max_steps: int = 8  # model calls per turn before giving up (loop guard)
    agent_max_output_tokens: int = 1024
    history_token_budget: int = 2500  # prior conversation sent to the model, newest first
    session_ttl_hours: int = 24

    # --- RAG -------------------------------------------------------------------
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384  # must match the vector(384) column in the migration
    embedding_query_instruction: str = "Represent this sentence for searching relevant passages: "
    embedding_cache_dir: Path = API_ROOT / ".cache" / "fastembed"
    knowledge_base_dir: Path = API_ROOT / "knowledge_base"
    chunk_max_tokens: int = 350
    chunk_overlap_tokens: int = 60
    retrieval_candidates: int = 20  # per retriever, before fusion
    retrieval_top_k: int = 5
    # Three-band confidence gate on top cosine similarity, calibrated by `uv run eval-retrieval`
    # (see eval/results/retrieval.md). Re-run the eval after changing the corpus or the model.
    retrieval_reject_below: float = 0.58
    retrieval_accept_above: float = 0.70

    # --- protection --------------------------------------------------------------
    rate_limit_per_minute: int = 20
    chaos_enabled: bool = False
    # Set to require `Authorization: Bearer <token>` on /api/observability (open in the demo).
    observability_token: SecretStr | None = None
    # Exposes the demo persona picker (fake customers, incl. their verification details).
    demo_mode: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
