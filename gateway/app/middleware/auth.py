"""API key authentication for OpenAI-compatible endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, Request, status

from app.config import Settings, get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


def extract_bearer_token(
    authorization: Optional[str],
    api_key_header: Optional[str],
) -> Optional[str]:
    """Extract API key from Authorization: Bearer or X-API-Key."""
    if api_key_header and api_key_header.strip():
        return api_key_header.strip()
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        # Some clients send the raw key in Authorization
        if len(parts) == 1:
            return parts[0].strip()
    return None


async def require_api_key(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> str:
    """FastAPI dependency: validate client API key.

    Open WebUI sends OPENAI_API_KEY as Bearer token.
    """
    settings: Settings = get_settings()
    token = extract_bearer_token(authorization, x_api_key)

    if not token:
        logger.warning("auth_missing", path=str(request.url.path))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Missing API key. Provide Authorization: Bearer <key>",
                    "type": "invalid_request_error",
                    "code": "missing_api_key",
                }
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    valid_keys = settings.api_keys
    if not valid_keys:
        # Misconfiguration: no keys defined — reject rather than open access
        logger.error("auth_no_keys_configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {
                    "message": "Gateway API keys are not configured",
                    "type": "server_error",
                    "code": "misconfigured",
                }
            },
        )

    if token not in valid_keys:
        logger.warning("auth_invalid_key", path=str(request.url.path))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Invalid API key",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Attach identity for rate limiting / logging
    request.state.api_key = token
    request.state.api_key_id = token[:8] + "..."
    return token
