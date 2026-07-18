"""Hermes Agent domain model."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator


class HermesAgent(BaseModel):
    """Runtime representation of a single Hermes Agent.

    Loaded from config/agents.yaml. No agent is hardcoded in application code.
    """

    id: str = Field(..., description="Stable agent identifier (used as model id)")
    name: str = Field(..., description="Display name shown in Open WebUI")
    description: str = Field(default="", description="Short description for model list")
    avatar: Optional[str] = Field(default=None, description="Emoji or URL")
    system_prompt: str = Field(default="", description="Injected system instructions")
    base_url: str = Field(..., description="Agent base URL (no trailing slash preferred)")
    endpoint: str = Field(default="/v1/chat/completions", description="Chat path")
    headers: Dict[str, str] = Field(default_factory=dict)
    api_key: Optional[str] = Field(default=None, description="Bearer token for agent API")
    model: str = Field(..., description="Upstream model name sent to the agent")
    enabled: bool = True
    timeout: int = 120
    stream: bool = True
    temperature: Optional[float] = 0.5
    # openai: POST OpenAI chat.completions payload to endpoint
    # message: POST {"message": "<last user text>"} (e.g. hr-ai-agent /v1/chat)
    api_style: str = Field(
        default="openai",
        description="Upstream API style: openai | message",
    )

    @field_validator("base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("endpoint")
    @classmethod
    def ensure_leading_slash(cls, value: str) -> str:
        if not value.startswith("/"):
            return f"/{value}"
        return value

    @field_validator("api_style")
    @classmethod
    def normalize_api_style(cls, value: str) -> str:
        style = (value or "openai").strip().lower()
        if style in {"simple", "hermes_simple", "hr"}:
            return "message"
        if style not in {"openai", "message"}:
            return "openai"
        return style

    @property
    def chat_url(self) -> str:
        """Full URL for chat completions."""
        return f"{self.base_url}{self.endpoint}"

    def auth_headers(self) -> Dict[str, str]:
        """Build outbound HTTP headers for the agent."""
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **self.headers,
        }
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        return headers

    def to_public_dict(self) -> Dict[str, Any]:
        """Safe view for admin/list APIs (no secrets)."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "avatar": self.avatar,
            "enabled": self.enabled,
            "model": self.model,
            "stream": self.stream,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "endpoint": self.endpoint,
            # base_url exposed without secrets for ops debugging
            "base_url": self.base_url,
        }
