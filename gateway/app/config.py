"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Gateway runtime configuration.

    All values are overridable via environment variables or a mounted .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Runtime
    environment: str = Field(default="local", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    gateway_host: str = Field(default="0.0.0.0", alias="GATEWAY_HOST")
    gateway_port_internal: int = Field(default=8000, alias="GATEWAY_PORT_INTERNAL")

    # Auth
    gateway_api_keys: str = Field(default="sk-gateway-dev-key", alias="GATEWAY_API_KEYS")
    jwt_secret: str = Field(default="change-me-jwt-secret", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=1440, alias="JWT_EXPIRE_MINUTES")

    # Rate limiting
    rate_limit_rpm: int = Field(default=120, alias="RATE_LIMIT_RPM")

    # CORS
    cors_origins: str = Field(
        default="http://localhost:3000",
        alias="CORS_ORIGINS",
    )

    # Agents
    agents_config_path: str = Field(
        default="/app/config/agents.yaml",
        alias="AGENTS_CONFIG_PATH",
    )
    default_agent_timeout: int = Field(default=120, alias="DEFAULT_AGENT_TIMEOUT")

    # Logging
    log_request_bodies: bool = Field(default=False, alias="LOG_REQUEST_BODIES")

    # Optional infra
    redis_enabled: bool = Field(default=True, alias="REDIS_ENABLED")
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")
    redis_password: str = Field(default="", alias="REDIS_PASSWORD")

    # Feature flags
    enable_mcp: bool = Field(default=False, alias="ENABLE_MCP")
    enable_rag: bool = Field(default=False, alias="ENABLE_RAG")

    @property
    def api_keys(self) -> List[str]:
        """Parse comma-separated API keys into a list."""
        return [k.strip() for k in self.gateway_api_keys.split(",") if k.strip()]

    @property
    def cors_origin_list(self) -> List[str]:
        """Parse comma-separated CORS origins."""
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return origins or ["*"]

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
