"""Health, readiness, and agent admin endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app import __version__
from app.config import get_settings
from app.middleware.auth import require_api_key
from app.models.schemas import AgentPublicInfo, HealthResponse
from app.services.agent_loader import registry
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Liveness / readiness probe (no auth — used by Docker healthchecks)."""
    settings = get_settings()
    redis_status = "disabled"
    if settings.redis_enabled:
        redis_client = getattr(request.app.state, "redis", None)
        if redis_client is None:
            redis_status = "unavailable"
        else:
            try:
                await redis_client.ping()
                redis_status = "ok"
            except Exception:
                redis_status = "error"

    return HealthResponse(
        status="ok",
        environment=settings.environment,
        version=__version__,
        agents_loaded=len(registry.list_all()),
        agents_enabled=registry.count_enabled(),
        redis=redis_status,
        features={
            "mcp": settings.enable_mcp,
            "rag": settings.enable_rag,
            "rate_limit": settings.rate_limit_rpm > 0,
        },
    )


@router.get("/ready")
async def ready() -> dict:
    """Readiness: requires at least one enabled agent."""
    if registry.count_enabled() < 1:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "reason": "no_enabled_agents"},
        )
    return {"status": "ready", "agents_enabled": registry.count_enabled()}


@router.get("/agents", response_model=list[AgentPublicInfo])
async def list_agents(_: str = Depends(require_api_key)) -> list[AgentPublicInfo]:
    """List configured agents (public fields only)."""
    return [
        AgentPublicInfo(
            id=a.id,
            name=a.name,
            description=a.description,
            avatar=a.avatar,
            enabled=a.enabled,
            model=a.model,
            stream=a.stream,
            temperature=a.temperature,
        )
        for a in registry.list_all()
    ]


@router.post("/agents/reload")
async def reload_agents(_: str = Depends(require_api_key)) -> dict:
    """Hot-reload agents.yaml without restarting the container."""
    try:
        count = registry.reload()
    except Exception as exc:
        logger.error("agents_reload_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"message": str(exc), "type": "server_error"}},
        ) from exc
    return {
        "status": "reloaded",
        "agents_loaded": count,
        "agents_enabled": registry.count_enabled(),
    }
