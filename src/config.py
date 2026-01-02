"""Configuration management using pydantic-settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Ghost Configuration
    ghost_webhook_secret: str
    ghost_url: str = ""
    ghost_admin_api_key: str = ""

    # Campaign Monitor Configuration
    cm_api_key: str
    cm_list_id: str

    # Redis Configuration
    redis_url: str = "redis://localhost:6379"

    # Application Configuration
    port: int = 3000
    log_level: str = "info"

    # Queue Configuration
    queue_name: str = "ghost-cm-sync"
    max_retries: int = 5
    retry_delays: list[int] = [1, 2, 4, 8, 16]

    # Circuit Breaker Configuration
    circuit_breaker_threshold: int = 10
    circuit_breaker_timeout: int = 300  # 5 minutes

    # API Timeouts
    cm_api_timeout: int = 10  # seconds


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
