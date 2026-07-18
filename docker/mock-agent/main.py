"""Minimal OpenAI-compatible mock Hermes agent for local development.

All agents can point here so the platform works without real backends.
Streaming SSE matches ChatGPT / Open WebUI expectations.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Mock Hermes Agent", version="1.0.0")


class ChatMessage(BaseModel):
    role: str
    content: Optional[Any] = None


class ChatRequest(BaseModel):
    model: str = "mock"
    messages: List[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    temperature: Optional[float] = None

    model_config = {"extra": "allow"}


def _last_user_text(messages: List[ChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "user":
            if isinstance(msg.content, str):
                return msg.content
            if isinstance(msg.content, list):
                parts = []
                for p in msg.content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(p.get("text", ""))
                return " ".join(parts)
    return ""


def _reply_text(model: str, user_text: str) -> str:
    preview = (user_text or "").strip().replace("\n", " ")
    if len(preview) > 200:
        preview = preview[:200] + "..."
    return (
        f"[Mock Hermes · model={model}] "
        f"I received your message and would route it to the real Hermes agent in production. "
        f"You said: {preview or '(empty)'}"
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "mock-hermes-agent"}


@app.get("/v1/models")
async def models() -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": "mock-default",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "mock",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(body: ChatRequest, request: Request):
    user_text = _last_user_text(body.messages)
    text = _reply_text(body.model, user_text)
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    if body.stream:
        return StreamingResponse(
            _stream(completion_id, created, body.model, text),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return JSONResponse(
        {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": body.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": max(1, len(user_text) // 4),
                "completion_tokens": max(1, len(text) // 4),
                "total_tokens": max(1, (len(user_text) + len(text)) // 4),
            },
        }
    )


async def _stream(
    completion_id: str,
    created: int,
    model: str,
    text: str,
) -> AsyncIterator[str]:
    # Role chunk
    first = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}
        ],
    }
    yield f"data: {json.dumps(first)}\n\n"
    await asyncio.sleep(0.02)

    # Content in small pieces (ChatGPT-like)
    words = text.split(" ")
    buf: List[str] = []
    for i, word in enumerate(words):
        buf.append(word)
        if len(buf) >= 3 or i == len(words) - 1:
            piece = (" ".join(buf) + (" " if i < len(words) - 1 else ""))
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            buf = []
            await asyncio.sleep(0.03)

    done = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(done)}\n\n"
    yield "data: [DONE]\n\n"
