"""Fetch raw attachment bytes from Open WebUI by file id.

Open WebUI never sends attachment bytes to an external OpenAI-compatible
endpoint — it keeps them in its own store and (at most) passes ids around.
This client turns an id back into the original bytes via

    GET {OPENWEBUI_BASE_URL}/api/v1/files/{id}/content

which requires a verified-user token. Configure OPENWEBUI_API_KEY with an
API key from an admin account so files uploaded by any user are readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import unquote

import httpx

from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RawFile:
    """An attachment resolved to bytes, ready to be posted upstream."""

    filename: str
    content: bytes
    content_type: str = "application/octet-stream"

    @property
    def size(self) -> int:
        return len(self.content)


def _safe_name(name: str) -> str:
    """Basename only — never let a stored name escape into a path."""
    cleaned = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return cleaned or "attachment.bin"


def _name_from_disposition(header: str) -> Optional[str]:
    """Pull the filename out of a Content-Disposition header."""
    if not header:
        return None
    for part in header.split(";"):
        part = part.strip()
        # RFC5987: filename*=UTF-8''name.pdf
        if part.lower().startswith("filename*="):
            value = part.split("=", 1)[1].strip()
            if "''" in value:
                value = value.split("''", 1)[1]
            return _safe_name(unquote(value.strip('"')))
        if part.lower().startswith("filename="):
            return _safe_name(unquote(part.split("=", 1)[1].strip().strip('"')))
    return None


class OpenWebUIFileClient:
    """Reads file content out of Open WebUI's file store."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        max_bytes: int = 52_428_800,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.max_bytes = max_bytes
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def start(self) -> None:
        if not self.enabled:
            return
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            follow_redirects=True,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch(
        self,
        file_id: str,
        fallback_name: str = "",
        fallback_type: str = "",
    ) -> Optional[RawFile]:
        """Download one file. Returns None instead of raising — a missing
        attachment should degrade the answer, not fail the whole chat."""
        if not self._client or not file_id:
            return None
        try:
            response = await self._client.get(
                f"/api/v1/files/{file_id}/content",
                params={"attachment": "true"},
            )
        except httpx.HTTPError as exc:
            logger.warning("owui_file_fetch_failed", file_id=file_id, error=str(exc))
            return None

        if response.status_code >= 400:
            logger.warning(
                "owui_file_fetch_error",
                file_id=file_id,
                status=response.status_code,
                body=response.text[:200],
            )
            return None

        content = response.content
        if not content:
            logger.warning("owui_file_empty", file_id=file_id)
            return None
        if len(content) > self.max_bytes:
            logger.warning(
                "owui_file_too_large",
                file_id=file_id,
                size=len(content),
                limit=self.max_bytes,
            )
            return None

        name = (
            _name_from_disposition(response.headers.get("content-disposition", ""))
            or _safe_name(fallback_name)
        )
        ctype = (
            fallback_type
            or response.headers.get("content-type", "").split(";")[0].strip()
            or "application/octet-stream"
        )
        logger.info(
            "owui_file_fetched",
            file_id=file_id,
            filename=name,
            size=len(content),
            content_type=ctype,
        )
        return RawFile(filename=name, content=content, content_type=ctype)


_client: Optional[OpenWebUIFileClient] = None


def get_file_client() -> Optional[OpenWebUIFileClient]:
    """Process-wide client, or None when passthrough is not configured."""
    return _client


async def start_file_client() -> Optional[OpenWebUIFileClient]:
    global _client
    settings = get_settings()
    client = OpenWebUIFileClient(
        base_url=settings.openwebui_base_url,
        api_key=settings.openwebui_api_key,
        max_bytes=settings.openwebui_file_max_bytes,
    )
    if not client.enabled:
        logger.info(
            "owui_files_disabled",
            reason="OPENWEBUI_API_KEY or OPENWEBUI_BASE_URL not set",
        )
        _client = None
        return None
    await client.start()
    _client = client
    logger.info("owui_files_ready", base_url=client.base_url)
    return client


async def stop_file_client() -> None:
    global _client
    if _client:
        await _client.stop()
        _client = None
