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

    database_url: str = "postgresql://postgres:123@localhost:5432/geoflow_portal"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    artifact_root: Path = Path("data/artifacts")
    max_download_bytes: int = 32 * 1024 * 1024

    cors_origins: list[str] = ["http://localhost:5173"]

    # Execution strategy. "sequential" walks the DAG in-process; "parallel"
    # dispatches each wave as Celery tasks and reschedules the orchestrator.
    # Sequential is the default deliberately: on Windows the worker runs
    # --pool=solo, where the orchestrator occupies the only slot and dispatched
    # children would never be picked up. Switch to parallel only on a prefork
    # worker with concurrency > 1.
    execution_strategy: str = "sequential"
    max_parallel_nodes: int = 4          # cap per wave; 0 = no cap
    orchestrator_poll_seconds: float = 2.0
    run_timeout_seconds: int = 3600      # a lost worker must not leave a run "running"

    # Planner. "static" uses the hand-written water-stress graph and needs no
    # LLM at all; "llm" plans from the node catalog; "dummy" is the test chain.
    planner_mode: str = "static"

    # LLM provider. Ollama and xAI both speak the OpenAI chat-completions wire
    # format, so only base_url/model/key differ.
    llm_provider: str = "ollama"
    llm_timeout_seconds: float = 120.0
    llm_max_repairs: int = 2        # validator round trips after the first try

    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3.1"

    xai_api_key: str | None = None
    xai_base_url: str = "https://api.x.ai/v1"
    xai_model: str = "grok-2-latest"

    # Auth. No default secret on purpose: a shipped default is a shipped
    # forged-token vulnerability, so the app refuses to mint tokens without it.
    jwt_secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12

    gee_service_account_email: str | None = None
    gee_key_path: Path | None = None
    gee_project: str | None = None

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()