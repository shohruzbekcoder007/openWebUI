"""FastAPI Gateway entrypoint — OpenAI-compatible API for Hermes Agents."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.v1 import chat, health, models
from app.config import get_settings
from app.services.agent_loader import registry
from app.services.openwebui_files import start_file_client, stop_file_client
from app.services.proxy import AgentProxy
from app.services.rate_limit import rate_limiter
from app.utils.logging import get_logger, setup_logging

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle."""
    logger.info(
        "gateway_starting",
        version=__version__,
        environment=settings.environment,
        agents_config=settings.agents_config_path,
    )

    # Load Hermes agents from config
    count = registry.load(settings.agents_config_path)
    logger.info("agents_loaded", count=count, enabled=registry.count_enabled())

    # HTTP proxy client
    proxy = AgentProxy(default_timeout=settings.default_agent_timeout)
    await proxy.start()
    app.state.proxy = proxy

    # Attachment passthrough: resolves Open WebUI file ids to raw bytes
    app.state.owui_files = await start_file_client()

    # Rate limiter
    rate_limiter.rpm = settings.rate_limit_rpm

    # Optional Redis
    app.state.redis = None
    if settings.redis_enabled:
        try:
            import redis.asyncio as redis_async

            redis_kwargs = {"decode_responses": True}
            url = settings.redis_url
            client = redis_async.from_url(url, **redis_kwargs)
            if settings.redis_password:
                # Password may already be in URL; set if provided separately
                pass
            await client.ping()
            app.state.redis = client
            await rate_limiter.attach_redis(client)
            logger.info("redis_connected", url=settings.redis_url.split("@")[-1])
        except Exception as exc:
            logger.warning("redis_unavailable", error=str(exc))
            app.state.redis = None

    logger.info("gateway_ready")
    yield

    # Shutdown
    await proxy.stop()
    await stop_file_client()
    if app.state.redis is not None:
        await app.state.redis.aclose()
    logger.info("gateway_stopped")


app = FastAPI(
    title="Hermes Gateway",
    description=(
        "OpenAI-compatible API gateway routing Open WebUI traffic to Hermes Agents. "
        "Open WebUI never calls Hermes directly."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS — Open WebUI browser origin(s)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Agent-Id", "X-RateLimit-Remaining"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "message": "Invalid request",
                "type": "invalid_request_error",
                "code": "validation_error",
                "details": exc.errors(),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    logger.exception("unhandled_error", path=str(request.url.path), error=str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": "Internal server error",
                "type": "server_error",
                "code": "internal_error",
            }
        },
    )


# Routes
# OpenAI-compatible surface under /v1
app.include_router(models.router, prefix="/v1")
app.include_router(chat.router, prefix="/v1")

# Health & ops (also under /v1 for convenience and at root)
app.include_router(health.router, prefix="/v1")
app.include_router(health.router, prefix="")


@app.get("/")
async def root() -> dict:
    return {
        "service": "hermes-gateway",
        "version": __version__,
        "docs": "/docs",
        "openai_base": "/v1",
        "health": "/health",
    }
