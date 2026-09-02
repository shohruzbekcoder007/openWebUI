"""
title: Hermes File Passthrough
author: hermes-gateway
version: 0.3.0
description: Reads attachments out of Open WebUI's own file store and hands the raw bytes to the Hermes Gateway, which forwards them to the agent as multipart. Safe to run globally - it acts only on the models listed in model_ids, and takes Open WebUI's retrieval out of the loop for those.
required_open_webui_version: 0.5.0
"""

# Why this filter exists
# ---------------------------------------------------------------------------
# Open WebUI does not send attachments to an external OpenAI-compatible
# endpoint. In utils/middleware.py the request is rewritten before it leaves:
#
#   process_chat_payload()  ->  form_data.pop("files")   # ids moved to metadata
#   routers/openai.py       ->  payload.pop("metadata")  # metadata dropped
#
# so by the time the Gateway is called, both the bytes and the ids are gone and
# only RAG-injected text remains. Filter inlets run *before* that first pop,
# which is the one point where the file is still reachable.
#
# This filter runs inside Open WebUI, so it can read the file store directly
# and put the bytes in the body itself. Unknown top-level keys survive the trip
# (the router builds its payload with `{**form_data}` and removes only
# `metadata`), so `hermes_files` arrives at the Gateway intact. The Gateway
# decodes it in _request_level_files and POSTs the bytes to the agent as
# multipart/form-data.
#
# Reading the bytes here is what removes the need for OPENWEBUI_API_KEY: the
# Gateway never has to call back into Open WebUI for the file. Leave
# fallback_to_ids on if you would rather have it fetch anything this filter
# could not read — that path does need the key.
#
# Retrieval is disabled here too, by clearing body["files"] once the bytes are
# safely copied. metadata["files"] is filled at middleware.py:2714 from the
# value popped at 2600 -- both after this inlet -- so it ends up empty and
# chat_completion_files_handler (2929) short-circuits: no chunking, no
# embedding, no "### Task: answer using the context" wrapper.
#
# Open WebUI has a sanctioned flag for this, `file_handler = True`, and it is
# deliberately NOT used. utils/filter.py:172 reads that flag before running the
# handler and without looking at the body, so on a global filter Open WebUI
# would strip files from *every* model -- breaking attachments for agents that
# still want them inline (agriculture/QXAudit, programmer, translator). Doing
# it inside inlet() keeps the decision per model, which is what makes running
# this filter globally safe.
#
# Install: Admin Panel -> Functions -> New Function -> paste -> Save -> enable,
# then turn Global on. `model_ids` below decides which models it actually acts
# on; every other model passes through untouched.

import asyncio
import base64
from pathlib import Path
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=0,
            description="Filter execution order (lower runs first).",
        )
        model_ids: str = Field(
            default="press_reliz",
            description=(
                "Comma-separated model ids this filter acts on. Every other "
                "model is passed through untouched, so the filter is safe to "
                "run globally. Leave empty to act on all models."
            ),
        )
        target_key: str = Field(
            default="hermes_files",
            description="Body key the Gateway reads the attachments from.",
        )
        max_file_bytes: int = Field(
            default=26_214_400,
            description=(
                "Skip files larger than this (bytes). Base64 inflates the "
                "request by about a third, so keep it well under the proxy's "
                "body limit. Default 25 MB."
            ),
        )
        max_total_bytes: int = Field(
            default=52_428_800,
            description="Stop attaching once the request reaches this size (bytes).",
        )
        fallback_to_ids: bool = Field(
            default=True,
            description=(
                "If a file cannot be read here, still pass its id so a Gateway "
                "configured with OPENWEBUI_API_KEY can download it itself."
            ),
        )
        bypass_retrieval: bool = Field(
            default=True,
            description=(
                "Clear the attachments once their bytes are copied, so Open "
                "WebUI skips embedding, retrieval and the '### Task:' context "
                "prompt. Only ever applied to the models in model_ids."
            ),
        )

    def __init__(self) -> None:
        self.valves = self.Valves()
        # Left False on purpose -- see the note at the top of the file. Open
        # WebUI reads this flag without looking at the body, so setting it
        # would strip files from every model this filter is attached to.
        self.file_handler = False

    # -- helpers ------------------------------------------------------------

    def _file_id(self, item: Any) -> Optional[str]:
        if isinstance(item, str):
            return item.strip() or None
        if not isinstance(item, dict):
            return None
        inner = item.get("file") if isinstance(item.get("file"), dict) else {}
        file_id = item.get("id") or inner.get("id")
        return str(file_id) if file_id else None

    def _meta(self, item: Any) -> dict:
        """Name and content type as Open WebUI recorded them."""
        if not isinstance(item, dict):
            return {"name": "", "content_type": ""}
        inner = item.get("file") if isinstance(item.get("file"), dict) else {}
        meta = inner.get("meta") if isinstance(inner.get("meta"), dict) else {}
        if not meta and isinstance(item.get("meta"), dict):
            meta = item["meta"]
        return {
            "name": (
                item.get("name")
                or inner.get("filename")
                or meta.get("name")
                or ""
            ),
            "content_type": meta.get("content_type") or "",
        }

    async def _read_bytes(self, file_id: str) -> Optional[Tuple[bytes, str, str]]:
        """Pull one file out of Open WebUI's store. None if unavailable."""
        try:
            from open_webui.models.files import Files
            from open_webui.storage.provider import Storage

            record = await Files.get_file_by_id(file_id)
            if not record or not record.path:
                return None

            local_path = await asyncio.to_thread(Storage.get_file, record.path)
            path = Path(local_path)
            if not path.is_file():
                return None
            if path.stat().st_size > self.valves.max_file_bytes:
                return None

            data = await asyncio.to_thread(path.read_bytes)
            file_meta = record.meta or {}
            return (
                data,
                file_meta.get("name") or record.filename or path.name,
                file_meta.get("content_type") or "application/octet-stream",
            )
        except Exception:
            # Never break the chat over an attachment.
            return None

    # -- inlet --------------------------------------------------------------

    def _handles(self, model_id: Any) -> bool:
        """Only touch the models this filter was pointed at."""
        wanted = [m.strip() for m in (self.valves.model_ids or "").split(",")]
        wanted = [m for m in wanted if m]
        if not wanted:
            return True
        model = str(model_id or "")
        # Open WebUI may prefix the id with the connection name (e.g. "hermes.press_reliz")
        return any(model == m or model.endswith(f".{m}") for m in wanted)

    async def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        if not self._handles(body.get("model")):
            return body

        files = body.get("files")
        if not isinstance(files, list) or not files:
            return body

        attached: List[dict] = []
        total = 0

        for item in files:
            file_id = self._file_id(item)
            if not file_id:
                continue

            loaded = None
            if total < self.valves.max_total_bytes:
                loaded = await self._read_bytes(file_id)

            if loaded:
                data, name, content_type = loaded
                total += len(data)
                attached.append(
                    {
                        "id": file_id,
                        "filename": name,
                        "media_type": content_type,
                        "encoding": "base64",
                        "content_base64": base64.b64encode(data).decode("ascii"),
                    }
                )
            elif self.valves.fallback_to_ids:
                # No bytes: leave a reference the Gateway can resolve itself.
                meta = self._meta(item)
                attached.append(
                    {
                        "id": file_id,
                        "name": meta["name"],
                        "content_type": meta["content_type"],
                        "type": "file",
                    }
                )

        if not attached:
            return body

        body[self.valves.target_key] = attached

        if self.valves.bypass_retrieval:
            # Emptied only after the bytes are safe. RAG is fed from this key
            # later in the pipeline, so leaving nothing here disables it -- for
            # this model alone, since we returned early for the others.
            body["files"] = []

        return body
