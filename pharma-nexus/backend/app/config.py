import logging

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # PostgreSQL
    database_url: str = Field(
        default="postgresql+asyncpg://pharma_nexus:change_me_in_production@localhost:5432/pharma_nexus",
        alias="DATABASE_URL",
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg2://pharma_nexus:change_me_in_production@localhost:5432/pharma_nexus",
        alias="DATABASE_URL_SYNC",
    )

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    celery_broker_url: str = Field(
        default="redis://localhost:6379/1", alias="CELERY_BROKER_URL"
    )
    celery_result_backend: str = Field(
        default="redis://localhost:6379/2", alias="CELERY_RESULT_BACKEND"
    )

    # Neo4j
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(
        default="change_me_in_production", alias="NEO4J_PASSWORD"
    )

    # Claude API
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    llm_cost_mode: str = Field(
        default="economy",
        alias="LLM_COST_MODE",
        description="LLM cost tier: economy (Haiku-only, confidence-only), "
        "standard (Sonnet bulk + Opus top), premium (Opus everything)",
    )

    # Model overrides — set these to use newer models without code changes
    model_haiku: str = Field(
        default="claude-haiku-4-5-20251001", alias="MODEL_HAIKU"
    )
    model_sonnet: str = Field(
        default="claude-sonnet-4-5-20250929", alias="MODEL_SONNET"
    )
    model_opus: str = Field(
        default="claude-opus-4-6", alias="MODEL_OPUS"
    )

    # NCBI / PubMed
    ncbi_api_key: str = Field(default="", alias="NCBI_API_KEY")
    ncbi_email: str = Field(default="", alias="NCBI_EMAIL")

    # Application
    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=True, alias="APP_DEBUG")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    app_secret_key: str = Field(
        default="change_me_in_production", alias="APP_SECRET_KEY"
    )

    # CORS
    cors_origins: str = Field(
        default="http://localhost:3000", alias="CORS_ORIGINS"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

_PLACEHOLDER = "change_me_in_production"

if settings.app_env != "development":
    _logger = logging.getLogger(__name__)
    _insecure = []
    if _PLACEHOLDER in settings.database_url:
        _insecure.append("DATABASE_URL")
    if _PLACEHOLDER in settings.database_url_sync:
        _insecure.append("DATABASE_URL_SYNC")
    if settings.neo4j_password == _PLACEHOLDER:
        _insecure.append("NEO4J_PASSWORD")
    if settings.app_secret_key == _PLACEHOLDER:
        _insecure.append("APP_SECRET_KEY")
    if _insecure:
        _logger.critical(
            "INSECURE DEFAULT CREDENTIALS detected for: %s. "
            "Set these via environment variables before deploying.",
            ", ".join(_insecure),
        )
