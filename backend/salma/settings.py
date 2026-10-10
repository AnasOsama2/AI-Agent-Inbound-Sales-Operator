from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = (
        "postgresql+psycopg://salma:salma_local@127.0.0.1:5433/salma?connect_timeout=5"
    )
    staff_token: SecretStr = SecretStr("")
    webhook_token: SecretStr = SecretStr("")
    chatwoot_url: str = "http://host.docker.internal:3001"
    chatwoot_account_id: int = 1
    chatwoot_inboxes: dict[int, str] = {}
    chatwoot_api_token: SecretStr = SecretStr("")
    live_enabled: bool = False
    agent_mode: Literal["simulator", "groq"] = "simulator"
    groq_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("GROQ_API_KEY", "groq_key")
    )
    groq_model: str = "qwen/qwen3.8-27b"
    groq_url: str = "https://api.groq.com/openai/v1"
    max_tool_rounds: int = 3
    redis_url: str = "redis://127.0.0.1:6380/0"
    credential_key: SecretStr = SecretStr("")
    evolution_url: str = "http://host.docker.internal:18080"
    evolution_api_key: SecretStr = SecretStr("")
    chatwoot_public_url: str = "http://localhost:3001"
    evolution_public_url: str = "http://localhost:18080"
    uploads_dir: str = "data/uploads"
    public_api_url: str = "http://host.docker.internal:8000"
    session_secure: bool = False
    legacy_api_enabled: bool = False
    lead_weights: dict[str, int] = {}
    lead_thresholds: dict[str, int] = {}


@lru_cache
def settings() -> Settings:
    return Settings()
