"""Proxy chat completion requests to Hermes Agents with streaming support."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Mapping, Optional

import httpx

from app.models.agent import HermesAgent
from app.services.openwebui_files import RawFile, get_file_client
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


def _decode_data_url(url: str) -> Optional[Dict[str, str]]:
    """Decode data:<mime>;base64,<payload> into filename + text content when possible."""
    import base64
    import binascii
    from urllib.parse import unquote

    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url.startswith("data:"):
        return None
    try:
        header, _, data = url.partition(",")
        if not data:
            return None
        mime = "application/octet-stream"
        if header.startswith("data:") and ";" in header:
            mime = header[5:].split(";", 1)[0] or mime
        raw: bytes
        if ";base64" in header.lower():
            raw = base64.b64decode(data, validate=False)
        else:
            raw = unquote(data).encode("utf-8", errors="replace")
        # Prefer text for logs / plain docs
        text: Optional[str] = None
        if mime.startswith("text/") or mime in {
            "application/json",
            "application/xml",
            "application/x-ndjson",
            "application/log",
        }:
            text = raw.decode("utf-8", errors="replace")
        else:
            # Heuristic: if mostly printable, treat as text (nginx logs often .log)
            sample = raw[:4000]
            if sample and sum(32 <= b < 127 or b in (9, 10, 13) for b in sample) / max(
                1, len(sample)
            ) > 0.85:
                text = raw.decode("utf-8", errors="replace")
        if text is None:
            return {
                "filename": "attachment.bin",
                "content_base64": base64.b64encode(raw).decode("ascii"),
                "encoding": "base64",
                "media_type": mime,
            }
        ext = ".txt"
        if "json" in mime:
            ext = ".json"
        elif "xml" in mime:
            ext = ".xml"
        elif "log" in mime or mime == "text/plain":
            ext = ".log"
        return {
            "filename": f"attachment{ext}",
            "content": text,
            "encoding": "utf-8",
            "media_type": mime,
        }
    except (binascii.Error, ValueError, UnicodeError):
        return None


def _part_to_attachments(part: Any) -> List[Dict[str, str]]:
    """Extract file-like parts Open WebUI may embed in multimodal content."""
    out: List[Dict[str, str]] = []
    if not isinstance(part, dict):
        return out

    ptype = str(part.get("type") or "").lower()

    # text parts are not files
    if ptype in {"text", "input_text"}:
        return out

    # OpenAI / OWUI style image_url or file_url with data:
    for key in ("image_url", "file_url", "input_audio"):
        block = part.get(key)
        url = None
        if isinstance(block, dict):
            url = block.get("url") or block.get("file_data")
        elif isinstance(block, str):
            url = block
        if url:
            decoded = _decode_data_url(str(url))
            if decoded:
                name = part.get("name") or part.get("filename")
                if name:
                    decoded["filename"] = str(PathName(name))
                out.append(decoded)

    # type: file / input_file
    if ptype in {"file", "input_file", "document"}:
        file_obj = part.get("file") if isinstance(part.get("file"), dict) else part
        name = (
            file_obj.get("filename")
            or file_obj.get("name")
            or part.get("filename")
            or part.get("name")
            or "upload.bin"
        )
        # inline base64
        b64 = (
            file_obj.get("file_data")
            or file_obj.get("data")
            or file_obj.get("content")
            or part.get("data")
        )
        url = file_obj.get("url") or part.get("url")
        if url and str(url).startswith("data:"):
            decoded = _decode_data_url(str(url))
            if decoded:
                decoded["filename"] = str(PathName(name))
                out.append(decoded)
                return out
        if isinstance(b64, str) and b64.strip():
            if b64.startswith("data:"):
                decoded = _decode_data_url(b64)
                if decoded:
                    decoded["filename"] = str(PathName(name))
                    out.append(decoded)
            else:
                import base64

                try:
                    raw = base64.b64decode(b64, validate=False)
                    text = raw.decode("utf-8", errors="replace")
                    out.append(
                        {
                            "filename": str(PathName(name)),
                            "content": text,
                            "encoding": "utf-8",
                        }
                    )
                except Exception:
                    out.append(
                        {
                            "filename": str(PathName(name)),
                            "content_base64": b64,
                            "encoding": "base64",
                        }
                    )
    return out


def PathName(name: str) -> str:
    """Basename only — avoid path traversal in attachment names."""
    return name.replace("\\", "/").rsplit("/", 1)[-1] or "upload.bin"


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"text", "input_text", None}:
                if part.get("type") in {"text", "input_text"} or "text" in part:
                    parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts).strip()
    return str(content)


def _content_to_attachments(content: Any) -> List[Dict[str, str]]:
    if not isinstance(content, list):
        return []
    found: List[Dict[str, str]] = []
    for part in content:
        found.extend(_part_to_attachments(part))
    return found


def _last_user_text(messages: List[ChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "user":
            return _content_to_text(msg.content)
    if messages:
        return _content_to_text(messages[-1].content)
    return ""


def _last_user_attachments(messages: List[ChatMessage]) -> List[Dict[str, str]]:
    for msg in reversed(messages):
        if msg.role == "user":
            return _content_to_attachments(msg.content)
    if messages:
        return _content_to_attachments(messages[-1].content)
    return []


def _request_level_files(request: ChatCompletionRequest) -> List[Dict[str, str]]:
    """Open WebUI sometimes puts files on the top-level request body.

    `hermes_files` is also read: the Hermes passthrough filter can load the
    bytes inside Open WebUI and hand them over base64-encoded, which removes
    the need for the Gateway to hold an Open WebUI API key.
    """
    extra = getattr(request, "model_extra", None) or {}
    buckets: List[Any] = []
    if isinstance(extra, dict):
        buckets.append(extra.get("files"))
        buckets.append(extra.get("hermes_files"))
    if hasattr(request, "files"):
        buckets.append(getattr(request, "files", None))

    out: List[Dict[str, str]] = []
    for raw_files in buckets:
        if not isinstance(raw_files, list):
            continue
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            name = (
                item.get("filename")
                or item.get("name")
                or item.get("id")
                or "upload.bin"
            )
            media_type = str(item.get("media_type") or item.get("content_type") or "")
            # Already has content
            if item.get("content"):
                entry = {
                    "filename": str(PathName(str(name))),
                    "content": str(item.get("content")),
                    "encoding": "utf-8",
                }
                if media_type:
                    entry["media_type"] = media_type
                out.append(entry)
                continue
            for key in ("url", "data", "file_data", "content_base64"):
                val = item.get(key)
                if not val:
                    continue
                if str(val).startswith("data:"):
                    decoded = _decode_data_url(str(val))
                    if decoded:
                        decoded["filename"] = str(PathName(str(name)))
                        if media_type:
                            decoded["media_type"] = media_type
                        out.append(decoded)
                elif key == "content_base64" or item.get("encoding") == "base64":
                    entry = {
                        "filename": str(PathName(str(name))),
                        "content_base64": str(val),
                        "encoding": "base64",
                    }
                    if media_type:
                        entry["media_type"] = media_type
                    out.append(entry)
                break
    return out


def _file_ref(item: Any) -> Optional[Dict[str, str]]:
    """Normalize one Open WebUI file entry into {id, name, content_type}.

    Entries arrive in several shapes depending on where they were picked up:
    a bare id string, {"id": ...}, or {"type": "file", "file": {...}}.
    Entries that already carry their content are skipped — those are handled
    by the inline path and must not be downloaded twice.
    """
    if isinstance(item, str):
        return {"id": item, "name": "", "content_type": ""} if item.strip() else None
    if not isinstance(item, dict):
        return None

    inner = item.get("file") if isinstance(item.get("file"), dict) else {}
    # Already inlined by Open WebUI or by the caller — not a reference.
    if item.get("content") or item.get("content_base64") or item.get("data"):
        return None

    file_id = (
        item.get("id")
        or inner.get("id")
        or item.get("file_id")
        or item.get("collection_name")
    )
    if not file_id or not str(file_id).strip():
        return None

    meta = inner.get("meta") if isinstance(inner.get("meta"), dict) else {}
    if not meta and isinstance(item.get("meta"), dict):
        meta = item["meta"]

    name = (
        item.get("filename")
        or item.get("name")
        or inner.get("filename")
        or inner.get("name")
        or meta.get("name")
        or ""
    )
    ctype = item.get("content_type") or meta.get("content_type") or ""
    return {
        "id": str(file_id).strip(),
        "name": str(name or ""),
        "content_type": str(ctype or ""),
    }


def _openwebui_file_refs(request: ChatCompletionRequest) -> List[Dict[str, str]]:
    """Collect referenced (not inlined) Open WebUI attachments.

    Open WebUI moves `files` into `metadata` and then drops `metadata` before
    calling an external OpenAI endpoint, so nothing here is guaranteed. Every
    place an id realistically survives is checked, including the custom keys a
    Filter function can use to smuggle them through.
    """
    extra = getattr(request, "model_extra", None) or {}
    meta = _metadata_dict(request)

    buckets: List[Any] = [
        extra.get("files"),
        extra.get("__files__"),
        extra.get("hermes_files"),
        meta.get("files"),
        meta.get("__files__"),
    ]
    for msg in request.messages:
        msg_extra = getattr(msg, "model_extra", None) or {}
        buckets.append(msg_extra.get("files"))

    refs: List[Dict[str, str]] = []
    seen: set = set()
    for bucket in buckets:
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            ref = _file_ref(item)
            if not ref or ref["id"] in seen:
                continue
            seen.add(ref["id"])
            refs.append(ref)
    return refs


def _attachment_to_raw(item: Dict[str, str]) -> Optional[RawFile]:
    """Turn one already-inlined attachment dict back into bytes."""
    import base64
    import binascii

    name = str(item.get("filename") or "attachment.bin")
    ctype = str(item.get("media_type") or "application/octet-stream")
    b64 = item.get("content_base64")
    if b64:
        try:
            return RawFile(
                filename=name,
                content=base64.b64decode(str(b64), validate=False),
                content_type=ctype,
            )
        except (binascii.Error, ValueError):
            return None
    text = item.get("content")
    if text:
        return RawFile(
            filename=name,
            content=str(text).encode("utf-8", errors="replace"),
            content_type=ctype or "text/plain",
        )
    return None


def _inline_raw_files(request: ChatCompletionRequest) -> List[RawFile]:
    """Attachments Open WebUI (or another client) embedded directly in the body."""
    items = _last_user_attachments(request.messages)
    items.extend(_request_level_files(request))
    out: List[RawFile] = []
    seen: set = set()
    for item in items:
        raw = _attachment_to_raw(item)
        if not raw:
            continue
        key = (raw.filename, raw.size)
        if key in seen:
            continue
        seen.add(key)
        out.append(raw)
    return out


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
        # hr-ai-agent / hermes style: POST /v1/chat
        # {"message": "...", "session_id": "...", "files":[{filename, content}]}
        user_text = _last_user_text(request.messages)
        attachments = _last_user_attachments(request.messages)
        attachments.extend(_request_level_files(request))
        # Deduplicate by filename+len
        seen = set()
        unique_files: List[Dict[str, str]] = []
        for f in attachments:
            key = (f.get("filename"), len(f.get("content") or f.get("content_base64") or ""))
            if key in seen:
                continue
            seen.add(key)
            unique_files.append(f)

        # If only files and empty text, still send a short instruction
        if not (user_text or "").strip() and unique_files:
            user_text = "Please analyze the attached file(s)."

        payload: Dict[str, Any] = {
            "message": user_text or "(empty)",
        }
        # multipart agents receive the bytes as form parts instead — inlining
        # them here as well would send every attachment twice.
        if agent.files_style == "none":
            unique_files = []
        if unique_files and not agent.sends_raw_files:
            payload["files"] = unique_files
            # Also inline small text files into message so older agents still work
            inline_parts: List[str] = []
            for f in unique_files:
                content = f.get("content")
                if not content:
                    continue
                # Cap inline size per file (512KB chars)
                if len(content) > 512_000:
                    content = content[:512_000] + "\n...[truncated]..."
                fname = f.get("filename") or "attachment.log"
                inline_parts.append(
                    f"\n\n----- BEGIN FILE: {fname} -----\n{content}\n----- END FILE: {fname} -----\n"
                )
            if inline_parts:
                payload["message"] = (payload["message"] or "").rstrip() + "".join(
                    inline_parts
                )

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
            attachment_count=len(unique_files),
            files_style=agent.files_style,
            message_len=len(payload.get("message") or ""),
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

    async def collect_raw_files(
        self,
        request: ChatCompletionRequest,
        agent: HermesAgent,
    ) -> List[RawFile]:
        """Resolve every attachment on the request to raw bytes.

        Two sources: files Open WebUI kept in its own store (we hold only the
        id and download the bytes) and files a client embedded in the body as
        data: URLs.
        """
        # Multipart form parts only make sense for the message API — an
        # OpenAI-style agent expects a JSON body with "messages".
        if not agent.sends_raw_files or agent.api_style != "message":
            return []

        raw: List[RawFile] = []
        refs = _openwebui_file_refs(request)
        if refs:
            client = get_file_client()
            if not client:
                logger.warning(
                    "owui_file_refs_unresolved",
                    agent_id=agent.id,
                    count=len(refs),
                    reason="OPENWEBUI_API_KEY / OPENWEBUI_BASE_URL not configured",
                )
            else:
                for ref in refs:
                    fetched = await client.fetch(
                        ref["id"],
                        fallback_name=ref.get("name", ""),
                        fallback_type=ref.get("content_type", ""),
                    )
                    if fetched:
                        raw.append(fetched)

        seen = {(f.filename, f.size) for f in raw}
        for inline in _inline_raw_files(request):
            key = (inline.filename, inline.size)
            if key not in seen:
                seen.add(key)
                raw.append(inline)
        return raw

    async def _post_upstream(
        self,
        agent: HermesAgent,
        payload: Dict[str, Any],
        raw_files: List[RawFile],
        timeout: int,
    ) -> httpx.Response:
        """POST the payload — multipart when there are bytes to carry."""
        if not raw_files:
            return await self.client.post(
                agent.chat_url,
                json=payload,
                headers=agent.auth_headers(),
                timeout=timeout,
            )

        form: Dict[str, str] = {}
        for key in ("message", "session_id", "chat_id", "reset_session"):
            value = payload.get(key)
            if value is None:
                continue
            form[key] = str(value).lower() if isinstance(value, bool) else str(value)

        parts = [
            ("file", (f.filename, f.content, f.content_type)) for f in raw_files
        ]
        return await self.client.post(
            agent.chat_url,
            data=form,
            files=parts,
            headers=agent.auth_headers(json_body=False),
            timeout=timeout,
        )

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

        raw_files = await self.collect_raw_files(request, agent)
        if raw_files and not str(payload.get("message") or "").strip("() "):
            # Attachment with no question of its own.
            payload["message"] = "Please analyze the attached file(s)."

        logger.info(
            "proxy_request",
            agent_id=agent.id,
            model=agent.model,
            stream=False,
            api_style=agent.api_style,
            url=agent.chat_url,
            transport="multipart" if raw_files else "json",
            raw_file_count=len(raw_files),
            raw_file_bytes=sum(f.size for f in raw_files),
            has_session_id=bool(payload.get("session_id"))
            if agent.api_style == "message"
            else None,
        )

        try:
            response = await self._post_upstream(agent, payload, raw_files, timeout)
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
