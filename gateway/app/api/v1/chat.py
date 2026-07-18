"""OpenAI-compatible /v1/chat/completions endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.middleware.auth import require_api_key
from app.models.schemas import ChatCompletionRequest
from app.services.agent_loader import registry
from app.services.proxy import AgentProxy, AgentProxyError
from app.services.rate_limit import rate_limiter
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["chat"])


def get_proxy(request: Request) -> AgentProxy:
    return request.app.state.proxy


@router.post("/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    api_key: str = Depends(require_api_key),
    proxy: AgentProxy = Depends(get_proxy),
):
    """Route chat completion to the Hermes Agent selected by `model`.

    Streaming responses use Server-Sent Events (text/event-stream),
    matching OpenAI / ChatGPT behavior expected by Open WebUI.
    """
    # Rate limit
    allowed, remaining = await rate_limiter.check(api_key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "message": "Rate limit exceeded. Try again later.",
                    "type": "rate_limit_error",
                    "code": "rate_limit_exceeded",
                }
            },
            headers={"Retry-After": "60", "X-RateLimit-Remaining": "0"},
        )

    agent = registry.resolve(body.model)
    if not agent:
        logger.warning("model_not_found", model=body.model)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "message": (
                        f"Model '{body.model}' not found. "
                        "Use GET /v1/models to list available Hermes agents."
                    ),
                    "type": "invalid_request_error",
                    "code": "model_not_found",
                }
            },
        )

    if not agent.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "message": f"Model '{body.model}' is disabled",
                    "type": "invalid_request_error",
                    "code": "model_disabled",
                }
            },
        )

    want_stream = bool(body.stream) if body.stream is not None else agent.stream

    logger.info(
        "chat_request",
        model=body.model,
        agent_id=agent.id,
        stream=want_stream,
        message_count=len(body.messages),
        client=request.client.host if request.client else None,
    )

    if want_stream:
        generator = proxy.stream(body, agent)
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
                "X-RateLimit-Remaining": str(remaining),
                "X-Agent-Id": agent.id,
            },
        )

    try:
        data = await proxy.complete(body, agent)
    except AgentProxyError as exc:
        raise HTTPException(
            status_code=exc.status_code if 400 <= exc.status_code < 600 else 502,
            detail={
                "error": {
                    "message": exc.message,
                    "type": "api_error",
                    "code": "upstream_error",
                }
            },
        ) from exc

    return JSONResponse(
        content=data,
        headers={
            "X-RateLimit-Remaining": str(remaining),
            "X-Agent-Id": agent.id,
        },
    )
