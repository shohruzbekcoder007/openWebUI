"""OpenAI-compatible /v1/models endpoints."""

from __future__ import annotations

import time
from typing import List

from fastapi import APIRouter, Depends

from app.middleware.auth import require_api_key
from app.models.schemas import ModelListResponse, ModelObject
from app.services.agent_loader import registry

router = APIRouter(tags=["models"])


def _agent_to_model(agent) -> ModelObject:
    created = int(time.time())
    return ModelObject(
        id=agent.id,
        created=created,
        owned_by="hermes",
        root=agent.id,
        name=agent.name,
        description=agent.description,
    )


@router.get("/models", response_model=ModelListResponse)
async def list_models(_: str = Depends(require_api_key)) -> ModelListResponse:
    """Return each enabled Hermes Agent as an OpenAI model.

    Open WebUI uses this for the model picker.
    """
    models: List[ModelObject] = [
        _agent_to_model(agent) for agent in registry.list_enabled()
    ]
    return ModelListResponse(data=models)


@router.get("/models/{model_id}", response_model=ModelObject)
async def get_model(model_id: str, _: str = Depends(require_api_key)) -> ModelObject:
    """Retrieve a single model (agent) by id or display name."""
    agent = registry.resolve(model_id)
    if not agent or not agent.enabled:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "message": f"Model '{model_id}' not found",
                    "type": "invalid_request_error",
                    "code": "model_not_found",
                }
            },
        )
    return _agent_to_model(agent)
