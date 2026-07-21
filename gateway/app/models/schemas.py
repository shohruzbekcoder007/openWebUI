"""OpenAI-compatible Pydantic schemas used by the Gateway API."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Chat completions
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: Optional[Union[str, List[Dict[str, Any]]]] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """Subset of OpenAI chat.completions request used by Open WebUI."""

    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    user: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    response_format: Optional[Dict[str, Any]] = None
    seed: Optional[int] = None
    # Pass-through for extra Open WebUI / provider fields
    metadata: Optional[Dict[str, Any]] = None
    # Open WebUI often sends these at the top level and/or inside metadata
    chat_id: Optional[str] = None
    session_id: Optional[str] = None
    # Assistant message id (Open WebUI); not used as session_id
    id: Optional[str] = None

    model_config = {"extra": "allow"}


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: Optional[str] = "stop"
    logprobs: Optional[Any] = None


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: Optional[UsageInfo] = None
    system_fingerprint: Optional[str] = None


class ChatCompletionChunkDelta(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class ChatCompletionChunkChoice(BaseModel):
    index: int = 0
    delta: ChatCompletionChunkDelta
    finish_reason: Optional[str] = None
    logprobs: Optional[Any] = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: List[ChatCompletionChunkChoice]
    system_fingerprint: Optional[str] = None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ModelObject(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str = "hermes"
    permission: List[Any] = Field(default_factory=list)
    root: Optional[str] = None
    parent: Optional[str] = None
    # Open WebUI-friendly extras (ignored by strict clients)
    name: Optional[str] = None
    description: Optional[str] = None


class ModelListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: List[ModelObject]


# ---------------------------------------------------------------------------
# Health / meta
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str
    agents_loaded: int
    agents_enabled: int
    redis: str
    features: Dict[str, bool]


class AgentPublicInfo(BaseModel):
    id: str
    name: str
    description: str
    avatar: Optional[str] = None
    enabled: bool
    model: str
    stream: bool
    temperature: Optional[float] = None
