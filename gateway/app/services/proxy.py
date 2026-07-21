"""Proxy chat completion requests to Hermes Agents with streaming support."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Mapping, Optional

import httpx

from app.models.agent import HermesAgent
from app.models.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Open WebUI forwards chat/session ids only via headers when
# ENABLE_FORWARD_USER_INFO_HEADERS=true (metadata is stripped from body).
OPENWEBUI_CHAT_ID_HEADERS = (
    "x-openwebui-chat-id",
    "x-chat-id",
)
OPENWEBUI_SESSION_ID_HEADERS = (
    "x-openwebui-session-id",
    "x-session-id",
)
OPENWEBUI_USER_ID_HEADERS = (
    "x-openwebui-user-id",
    "x-user-id",
)

HeaderMap = Mapping[str, str]


def _message_to_dict(msg: ChatMessage) -> Dict[str, Any]:
    data = msg.model_dump(exclude_none=True)
    return data


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts).strip()
    return str(content)


def _last_user_text(messages: List[ChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "user":
            return _content_to_text(msg.content)
    if messages:
        return _content_to_text(messages[-1].content)
    return ""


def _first_user_text(messages: List[ChatMessage]) -> str:
    for msg in messages:
        if msg.role == "user":
            return _content_to_text(msg.content)
    return ""


def _metadata_dict(request: ChatCompletionRequest) -> Dict[str, Any]:
    meta = request.metadata
    if isinstance(meta, dict):
        return meta
    return {}


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _header_value(headers: Optional[HeaderMap], *names: str) -> Optional[str]:
    if not headers:
        return None
    # Starlette Headers are case-insensitive; plain dicts may not be.
    lower_map: Optional[Dict[str, str]] = None
    for name in names:
        try:
            value = headers.get(name)  # type: ignore[attr-defined]
        except Exception:
            value = None
        if value:
            return str(value).strip()
        if lower_map is None:
            try:
                lower_map = {str(k).lower(): str(v) for k, v in headers.items()}
            except Exception:
                lower_map = {}
        value = lower_map.get(name.lower()) if lower_map else None
        if value:
            return value.strip()
    return None


def conversation_fingerprint(request: ChatCompletionRequest) -> Optional[str]:
    """Stable fallback id when Open WebUI does not forward chat_id.

    Uses model + first user message. Same chat thread keeps the same first
    user turn, so multi-turn requests map to one session. Collisions are
    possible for identical openers — prefer real chat_id headers.
    """
    first = _first_user_text(request.messages)
    if not first:
        return None
    raw = f"{request.model}|{first}".encode("utf-8")
    return "owui-" + hashlib.sha256(raw).hexdigest()[:32]


def resolve_chat_id(
    request: ChatCompletionRequest,
    headers: Optional[HeaderMap] = None,
) -> Optional[str]:
    """Open WebUI chat id from body, metadata, or forward headers."""
    meta = _metadata_dict(request)
    extra = getattr(request, "model_extra", None) or {}
    return _first_non_empty(
        request.chat_id,
        meta.get("chat_id"),
        extra.get("chat_id") if isinstance(extra, dict) else None,
        _header_value(headers, *OPENWEBUI_CHAT_ID_HEADERS),
    )


def resolve_session_id(
    request: ChatCompletionRequest,
    headers: Optional[HeaderMap] = None,
    *,
    allow_fingerprint: bool = True,
) -> Optional[str]:
    """Session id for upstream agents.

    Preference:
      body/metadata session_id
      → Open WebUI session/chat headers
      → body/metadata chat_id
      → user id
      → conversation fingerprint (optional fallback)
    """
    meta = _metadata_dict(request)
    extra = getattr(request, "model_extra", None) or {}
    resolved = _first_non_empty(
        request.session_id,
        meta.get("session_id"),
        extra.get("session_id") if isinstance(extra, dict) else None,
        _header_value(headers, *OPENWEBUI_SESSION_ID_HEADERS),
        resolve_chat_id(request, headers),
        request.user if isinstance(request.user, str) else None,
        meta.get("user_id"),
        _header_value(headers, *OPENWEBUI_USER_ID_HEADERS),
    )
    if resolved:
        return resolved
    if allow_fingerprint:
        return conversation_fingerprint(request)
    return None


def enrich_request_ids_from_headers(
    request: ChatCompletionRequest,
    headers: Optional[HeaderMap],
) -> ChatCompletionRequest:
    """Fill chat_id/session_id on the body from Open WebUI headers if missing."""
    if not headers:
        return request
    chat_id = resolve_chat_id(request, headers)
    session_id = resolve_session_id(request, headers, allow_fingerprint=False)
    if chat_id and not request.chat_id:
        request.chat_id = chat_id
    if session_id and not request.session_id:
        request.session_id = session_id
    elif chat_id and not request.session_id:
        request.session_id = chat_id
    return request


def build_upstream_payload(
    request: ChatCompletionRequest,
    agent: HermesAgent,
    headers: Optional[HeaderMap] = None,
) -> Dict[str, Any]:
    """Build payload for the upstream agent (OpenAI or simple message style)."""
    if agent.api_style == "message":
        # hr-ai-agent style: POST /v1/chat
        # {"message": "...", "session_id": "<open-webui chat/session>"}
        payload: Dict[str, Any] = {
            "message": _last_user_text(request.messages) or "(empty)",
        }
        explicit_session = resolve_session_id(
            request, headers, allow_fingerprint=False
        )
        session_id = explicit_session or resolve_session_id(
            request, headers, allow_fingerprint=True
        )
        chat_id = resolve_chat_id(request, headers)
        if session_id:
            payload["session_id"] = session_id
        # Also forward chat_id when present and distinct (debugging / agents that need both)
        if chat_id and chat_id != session_id:
            payload["chat_id"] = chat_id
        elif chat_id and "session_id" not in payload:
            payload["session_id"] = chat_id
        logger.info(
            "message_style_payload",
            agent_id=agent.id,
            has_session_id=bool(payload.get("session_id")),
            has_chat_id=bool(chat_id),
            session_source=(
                "header_or_body"
                if explicit_session
                else ("fingerprint" if session_id else "none")
            ),
        )
        return payload

    messages: List[Dict[str, Any]] = [_message_to_dict(m) for m in request.messages]

    # Inject agent system prompt if not already present as first system message
    if agent.system_prompt:
        has_system = any(m.get("role") == "system" for m in messages)
        if has_system:
            # Prepend agent identity to the first system message
            for m in messages:
                if m.get("role") == "system":
                    existing = m.get("content") or ""
                    if isinstance(existing, str):
                        m["content"] = f"{agent.system_prompt.strip()}\n\n{existing}"
                    break
        else:
            messages.insert(
                0,
                {"role": "system", "content": agent.system_prompt.strip()},
            )

    temperature = (
        request.temperature if request.temperature is not None else agent.temperature
    )
    stream = bool(request.stream) if request.stream is not None else agent.stream

    payload: Dict[str, Any] = {
        "model": agent.model,
        "messages": messages,
        "stream": stream,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if request.top_p is not None:
        payload["top_p"] = request.top_p
    if request.max_tokens is not None:
        payload["max_tokens"] = request.max_tokens
    if request.stop is not None:
        payload["stop"] = request.stop
    if request.n is not None:
        payload["n"] = request.n
    if request.presence_penalty is not None:
        payload["presence_penalty"] = request.presence_penalty
    if request.frequency_penalty is not None:
        payload["frequency_penalty"] = request.frequency_penalty
    if request.user is not None:
        payload["user"] = request.user
    if request.tools is not None:
        payload["tools"] = request.tools
    if request.tool_choice is not None:
        payload["tool_choice"] = request.tool_choice
    if request.response_format is not None:
        payload["response_format"] = request.response_format
    if request.seed is not None:
        payload["seed"] = request.seed

    return payload


def _openai_sse_from_text(display_model: str, text: str) -> List[str]:
    """Turn a full assistant reply into OpenAI-style SSE chunks for Open WebUI."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    lines: List[str] = []
    first = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": display_model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None,
            }
        ],
    }
    lines.append(f"data: {json.dumps(first)}\n\n")
    content_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": display_model,
        "choices": [
            {"index": 0, "delta": {"content": text}, "finish_reason": None}
        ],
    }
    lines.append(f"data: {json.dumps(content_chunk)}\n\n")
    done = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": display_model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    lines.append(f"data: {json.dumps(done)}\n\n")
    lines.append("data: [DONE]\n\n")
    return lines


def _normalize_non_stream_response(
    data: Dict[str, Any],
    display_model: str,
) -> Dict[str, Any]:
    """Ensure response looks OpenAI-compatible and uses the UI model id."""
    if "id" not in data:
        data["id"] = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    if "object" not in data:
        data["object"] = "chat.completion"
    if "created" not in data:
        data["created"] = int(time.time())
    data["model"] = display_model

    # Minimal choices fallback for non-standard agents
    if "choices" not in data or not data["choices"]:
        content = (
            data.get("content")
            or data.get("response")
            or data.get("text")
            or data.get("output")
            or ""
        )
        if isinstance(content, dict):
            content = content.get("text") or json.dumps(content)
        data["choices"] = [
            {
                "index": 0,
                "message": {"role": "assistant", "content": str(content)},
                "finish_reason": "stop",
            }
        ]
    return data


def _synthetic_error_chunk(model: str, message: str) -> str:
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": f"[Gateway Error] {message}"},
                "finish_reason": "stop",
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n"


class AgentProxy:
    """HTTP client that forwards chat requests to Hermes Agents."""

    def __init__(self, default_timeout: int = 120) -> None:
        self.default_timeout = default_timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.default_timeout, connect=10.0),
            follow_redirects=True,
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if not self._client:
            raise RuntimeError("AgentProxy not started")
        return self._client

    async def complete(
        self,
        request: ChatCompletionRequest,
        agent: HermesAgent,
        headers: Optional[HeaderMap] = None,
    ) -> Dict[str, Any]:
        """Non-streaming chat completion."""
        payload = build_upstream_payload(request, agent, headers=headers)
        if agent.api_style == "openai":
            payload["stream"] = False
        timeout = agent.timeout or self.default_timeout

        logger.info(
            "proxy_request",
            agent_id=agent.id,
            model=agent.model,
            stream=False,
            api_style=agent.api_style,
            url=agent.chat_url,
            has_session_id=bool(payload.get("session_id"))
            if agent.api_style == "message"
            else None,
        )

        try:
            response = await self.client.post(
                agent.chat_url,
                json=payload,
                headers=agent.auth_headers(),
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            logger.error("proxy_timeout", agent_id=agent.id, error=str(exc))
            raise AgentProxyError(
                status_code=504,
                message=f"Agent '{agent.name}' timed out after {timeout}s",
            ) from exc
        except httpx.RequestError as exc:
            logger.error("proxy_connect_error", agent_id=agent.id, error=str(exc))
            raise AgentProxyError(
                status_code=502,
                message=f"Cannot reach agent '{agent.name}': {exc}",
            ) from exc

        if response.status_code >= 400:
            body = response.text[:2000]
            logger.error(
                "proxy_upstream_error",
                agent_id=agent.id,
                status=response.status_code,
                body=body,
            )
            raise AgentProxyError(
                status_code=response.status_code,
                message=f"Agent '{agent.name}' error: {body}",
            )

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise AgentProxyError(
                status_code=502,
                message=f"Agent '{agent.name}' returned invalid JSON",
            ) from exc

        if agent.api_style == "message" and data.get("success") is False:
            err = data.get("error") or "Agent returned success=false"
            raise AgentProxyError(status_code=502, message=str(err))

        return _normalize_non_stream_response(data, display_model=request.model)

    async def stream(
        self,
        request: ChatCompletionRequest,
        agent: HermesAgent,
        headers: Optional[HeaderMap] = None,
    ) -> AsyncIterator[str]:
        """Stream SSE chunks from the Hermes agent (OpenAI-compatible)."""
        display_model = request.model

        # Simple message APIs (e.g. /v1/chat) are non-streaming — call once and
        # synthesize OpenAI SSE so Open WebUI still gets a stream.
        if agent.api_style == "message":
            try:
                data = await self.complete(request, agent, headers=headers)
            except AgentProxyError as exc:
                yield _synthetic_error_chunk(display_model, exc.message)
                yield "data: [DONE]\n\n"
                return
            text = ""
            choices = data.get("choices") or []
            if choices:
                text = str((choices[0].get("message") or {}).get("content") or "")
            if not text:
                text = str(
                    data.get("response")
                    or data.get("content")
                    or data.get("text")
                    or ""
                )
            for line in _openai_sse_from_text(display_model, text):
                yield line
            return

        payload = build_upstream_payload(request, agent, headers=headers)
        payload["stream"] = True
        timeout = agent.timeout or self.default_timeout

        logger.info(
            "proxy_stream_start",
            agent_id=agent.id,
            model=agent.model,
            api_style=agent.api_style,
            url=agent.chat_url,
        )

        try:
            async with self.client.stream(
                "POST",
                agent.chat_url,
                json=payload,
                headers={
                    **agent.auth_headers(),
                    "Accept": "text/event-stream",
                },
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    logger.error(
                        "proxy_stream_error",
                        agent_id=agent.id,
                        status=response.status_code,
                        body=body[:1000],
                    )
                    yield _synthetic_error_chunk(
                        display_model,
                        f"Agent '{agent.name}' HTTP {response.status_code}: {body[:500]}",
                    )
                    yield "data: [DONE]\n\n"
                    return

                async for line in response.aiter_lines():
                    if line is None:
                        continue
                    text = line.strip()
                    if not text:
                        # Preserve blank line separators expected by SSE
                        yield "\n"
                        continue

                    # Already SSE-formatted
                    if text.startswith("data:"):
                        # Rewrite model field when possible
                        yield self._rewrite_sse_model(text, display_model) + "\n"
                        continue

                    # Plain JSON line from non-standard streams
                    if text.startswith("{") and text.endswith("}"):
                        try:
                            obj = json.loads(text)
                            obj["model"] = display_model
                            if "object" not in obj:
                                obj["object"] = "chat.completion.chunk"
                            yield f"data: {json.dumps(obj)}\n\n"
                            continue
                        except json.JSONDecodeError:
                            pass

                    # Pass through event: / id: etc.
                    yield text + "\n"

                # Ensure stream termination for clients that rely on [DONE]
                yield "data: [DONE]\n\n"

        except httpx.TimeoutException:
            logger.error("proxy_stream_timeout", agent_id=agent.id)
            yield _synthetic_error_chunk(
                display_model,
                f"Agent '{agent.name}' timed out after {timeout}s",
            )
            yield "data: [DONE]\n\n"
        except httpx.RequestError as exc:
            logger.error("proxy_stream_connect_error", agent_id=agent.id, error=str(exc))
            yield _synthetic_error_chunk(
                display_model,
                f"Cannot reach agent '{agent.name}': {exc}",
            )
            yield "data: [DONE]\n\n"

    @staticmethod
    def _rewrite_sse_model(line: str, display_model: str) -> str:
        """Replace upstream model id with the Open WebUI model id in SSE data lines."""
        if line.strip() == "data: [DONE]":
            return line
        if not line.startswith("data:"):
            return line
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            return line
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                obj["model"] = display_model
                return f"data: {json.dumps(obj)}"
        except json.JSONDecodeError:
            pass
        return line


class AgentProxyError(Exception):
    """Raised when an upstream agent call fails."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)
