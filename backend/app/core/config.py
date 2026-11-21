"""Application settings, loaded from environment variables (and an optional .env file).

Only settings that are consumed by code live here; each is documented in `.env.example`.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-0123456789"  # noqa: S105


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class StorageBackend(StrEnum):
    LOCAL = "local"
    S3 = "s3"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCUMIND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- service -------------------------------------------------------------------------
    app_name: str = "DocuMind AI"
    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"
    log_json: bool = True
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    request_id_header: str = "X-Request-ID"

    # --- data stores ---------------------------------------------------------------------
    # The app connects as a non-owner role so Row-Level Security applies (ADR 0008);
    # migrations run as the owner role.
    database_url: str = "postgresql+psycopg://documind_app:documind_dev_app@localhost:5433/documind"
    migration_database_url: str = (
        "postgresql+psycopg://documind:documind_dev_owner@localhost:5433/documind"
    )
    db_pool_size: int = 10
    redis_url: str = "redis://localhost:6380/0"
    celery_task_always_eager: bool = False  # tests only: run tasks inline
    worker_tmp_dir: Path | None = None  # scratch space for downloaded originals

    # --- auth ----------------------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr(_INSECURE_DEV_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_seconds: int = 14 * 24 * 3600

    # --- storage & uploads ---------------------------------------------------------------
    storage_backend: StorageBackend = StorageBackend.LOCAL
    storage_local_root: Path = Path("./.data/storage")
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_endpoint_url: str | None = None  # for S3-compatible test endpoints
    max_upload_bytes: int = 50 * 1024 * 1024
    max_pages_per_document: int = 2000

    # --- model providers (ADR 0009) -------------------------------------------------------
    # Read from GEMINI_API_KEY (conventional name) or DOCUMIND_GEMINI_API_KEY.
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GEMINI_API_KEY", "DOCUMIND_GEMINI_API_KEY")
    )
    # Model IDs are configuration, not code. Verify availability for your key/region.
    vlm_model: str = "gemini-2.5-flash"
    llm_model: str = "gemini-2.5-flash"
    llm_timeout_seconds: float = 60.0
    figure_analysis_concurrency: int = 4
    embedding_provider: str = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chunking_strategy: str = "parent_child"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L6-v2"
    rerank_enabled: bool = True
    # Optional, for cost reporting. Set from your provider's current price list; never guessed.
    llm_input_cost_per_mtok: float | None = None
    llm_output_cost_per_mtok: float | None = None

    # --- rate limiting -------------------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120
    query_rate_limit_per_minute: int = 20
    auth_rate_limit_per_minute: int = 10
    upload_rate_limit_per_minute: int = 30

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid log level: {value!r}")
        return level

    @field_validator("api_prefix")
    @classmethod
    def _validate_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("api_prefix must start with '/' and not end with '/'")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def _validate_jwt_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("jwt_secret must be at least 32 characters")
        return value

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.environment in {Environment.STAGING, Environment.PRODUCTION}:
            if self.jwt_secret.get_secret_value() == _INSECURE_DEV_JWT_SECRET:
                raise ValueError("DOCUMIND_JWT_SECRET must be set outside local/test")
            if self.storage_backend is StorageBackend.S3 and not self.s3_bucket:
                raise ValueError("DOCUMIND_S3_BUCKET is required for the s3 storage backend")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    return Settings()
