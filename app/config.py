from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single source of truth for configuration. Reads .env, then real env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: str = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql://postgres:hooria.1234@localhost:5432/geoflow"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    artifact_root: Path = Path("data/artifacts")
    max_download_bytes: int = 32 * 1024 * 1024

    cors_origins: list[str] = ["http://localhost:5173"]

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    gee_service_account_email: str | None = None
    gee_key_path: Path | None = None
    gee_project: str | None = None

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()