"""Configuration management using pydantic-settings."""

import os
import re
from functools import lru_cache

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class SiteConfig(BaseModel):
    """Configuration for a single Ghost site."""

    site_id: str
    name: str
    ghost_webhook_secret: str
    ghost_url: str = ""
    ghost_admin_api_key: str = ""
    cm_list_id: str


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Campaign Monitor Configuration (shared across sites)
    cm_api_key: str

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


def _load_sites_from_env() -> dict[str, SiteConfig]:
    """
    Load site configurations from environment variables.

    Expects environment variables in the format:
        SITE1_NAME=mainblog
        SITE1_GHOST_WEBHOOK_SECRET=secret1
        SITE1_GHOST_URL=https://blog1.example.com
        SITE1_GHOST_ADMIN_API_KEY=id:secret
        SITE1_CM_LIST_ID=list-id-1

        SITE2_NAME=newsletter
        SITE2_GHOST_WEBHOOK_SECRET=secret2
        ...

    Returns:
        Dictionary mapping site name to SiteConfig
    """
    sites: dict[str, SiteConfig] = {}

    # Find all SITE*_NAME variables to discover sites
    site_pattern = re.compile(r"^SITE(\d+)_NAME$", re.IGNORECASE)

    site_numbers: set[str] = set()
    for key in os.environ:
        match = site_pattern.match(key)
        if match:
            site_numbers.add(match.group(1))

    for num in sorted(site_numbers):
        prefix = f"SITE{num}_"

        name = os.environ.get(f"{prefix}NAME", "").strip()
        if not name:
            continue

        webhook_secret = os.environ.get(f"{prefix}GHOST_WEBHOOK_SECRET", "").strip()
        if not webhook_secret:
            continue

        cm_list_id = os.environ.get(f"{prefix}CM_LIST_ID", "").strip()
        if not cm_list_id:
            continue

        site_config = SiteConfig(
            site_id=name,
            name=name,
            ghost_webhook_secret=webhook_secret,
            ghost_url=os.environ.get(f"{prefix}GHOST_URL", "").strip(),
            ghost_admin_api_key=os.environ.get(f"{prefix}GHOST_ADMIN_API_KEY", "").strip(),
            cm_list_id=cm_list_id,
        )

        sites[name] = site_config

    return sites


@lru_cache
def get_all_sites() -> dict[str, SiteConfig]:
    """Get all configured sites."""
    return _load_sites_from_env()


def get_site_config(site_id: str) -> SiteConfig | None:
    """
    Get configuration for a specific site.

    Args:
        site_id: The site identifier (name)

    Returns:
        SiteConfig if found, None otherwise
    """
    sites = get_all_sites()
    return sites.get(site_id)


def get_site_ids() -> list[str]:
    """Get list of all configured site IDs."""
    return list(get_all_sites().keys())
