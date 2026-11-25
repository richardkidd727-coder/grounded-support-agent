"""Application settings, loaded from environment variables (and `.env` in local dev)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # Model ids are config, not code: confirm the current free models in AI Studio / Groq console.
    gemini_api_key: SecretStr | None = None
    gemini_chat_model: str = "gemini-2.5-flash"
    gemini_judge_model: str = "gemini-2.5-flash-lite"
    gemini_live_model: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    groq_api_key: SecretStr | None = None
    groq_model: str = "llama-3.3-70b-versatile"

    # --- RAG -------------------------------------------------------------------
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # --- protection --------------------------------------------------------------
    rate_limit_per_minute: int = 20
    chaos_enabled: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
