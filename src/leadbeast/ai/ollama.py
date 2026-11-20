"""ModelClient for a local Ollama server. A hosted model is a swap behind the same protocol."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import TypeAdapter

from leadbeast.core.config import get_settings
from leadbeast.core.ports import Status


class OllamaClient:
    name = "ollama"

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 client: httpx.AsyncClient | None = None):
        s = get_settings()
        self.base = (base_url or s.ollama_url).rstrip("/")
        self.model = model or s.ollama_model
        self.version = self.model
        self._client = client or httpx.AsyncClient(timeout=120)

    async def probe(self) -> Status:
        t0 = time.monotonic()
        try:
            r = await self._client.get(f"{self.base}/api/tags")
            ms = int((time.monotonic() - t0) * 1000)
            names = [m.get("name") for m in r.json().get("models", [])]
            if self.model not in names:
                return Status("slow", ms, f"model {self.model} not pulled")
            return Status("ok", ms)
        except (httpx.HTTPError, ValueError) as e:
            return Status("off", None, str(e))

    async def _generate(self, prompt: str, fmt: Any = None, max_tokens: int = 800) -> str:
        body: dict[str, Any] = {
            "model": self.model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0, "num_predict": max_tokens},
        }
        if fmt is not None:
            body["format"] = fmt
        r = await self._client.post(f"{self.base}/api/generate", json=body)
        r.raise_for_status()
        return str(r.json().get("response", ""))

    async def json(self, prompt: str, schema: type) -> object:
        adapter: TypeAdapter[Any] = TypeAdapter(schema)
        raw = await self._generate(prompt, fmt=adapter.json_schema())
        return adapter.validate_python(json.loads(raw))

    async def text(self, prompt: str, max_tokens: int) -> str:
        return await self._generate(prompt, max_tokens=max_tokens)
