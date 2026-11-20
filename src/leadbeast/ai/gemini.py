"""ModelClient for Google's hosted Gemini API (REST, no SDK). Same protocol as OllamaClient."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import TypeAdapter
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from leadbeast.core.config import get_settings
from leadbeast.core.ports import Status


class GeminiError(RuntimeError):
    pass


def _retryable(e: BaseException) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(e, httpx.TransportError)


class GeminiClient:
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 client: httpx.AsyncClient | None = None):
        s = get_settings()
        self.api_key = api_key or s.gemini_api_key.get_secret_value()
        if not self.api_key:
            raise GeminiError("No Gemini key: set GEMINI_API_KEY (or LB_GEMINI_API_KEY) in .env")
        self.model = model or s.gemini_model
        self.fallback_model = s.gemini_fallback_model if model is None else ""
        self.version = self.model
        self.base = s.gemini_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=60)

    @property
    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key, "content-type": "application/json"}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def probe(self) -> Status:
        t0 = time.monotonic()
        try:
            r = await self._client.get(f"{self.base}/models/{self.model}", headers=self._headers)
            ms = int((time.monotonic() - t0) * 1000)
            if r.status_code == 200:
                return Status("ok", ms)
            return Status("off", ms, f"HTTP {r.status_code}")
        except httpx.HTTPError as e:
            return Status("off", None, str(e))

    @retry(retry=retry_if_exception(_retryable), stop=stop_after_attempt(4),
           wait=wait_exponential(multiplier=2, min=2, max=30), reraise=True)
    async def _call(self, model: str, prompt: str, max_tokens: int,
                    schema: dict[str, Any] | None = None) -> str:
        # Newer Gemini models spend part of the output budget on internal "thinking";
        # leave headroom so a short answer is never cut to nothing.
        gen: dict[str, Any] = {"temperature": 0.2, "maxOutputTokens": max_tokens * 4 + 1024}
        if schema is not None:
            gen["responseMimeType"] = "application/json"
            gen["responseJsonSchema"] = schema
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": gen}
        r = await self._client.post(f"{self.base}/models/{model}:generateContent",
                                    headers=self._headers, json=body)
        r.raise_for_status()
        data = r.json()
        cands = data.get("candidates") or []
        if not cands:
            raise GeminiError(f"no candidates (feedback: {data.get('promptFeedback')})")
        parts = cands[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        if not text:
            raise GeminiError(f"empty answer (finishReason={cands[0].get('finishReason')})")
        return text

    async def _generate(self, prompt: str, max_tokens: int,
                        schema: dict[str, Any] | None = None) -> str:
        """Main model (with retries); if it stays overloaded, one pass on the fallback model."""
        try:
            return await self._call(self.model, prompt, max_tokens, schema)
        except Exception as e:
            if not (self.fallback_model and _retryable(e)):
                raise
            return await self._call(self.fallback_model, prompt, max_tokens, schema)

    async def json(self, prompt: str, schema: type) -> object:
        adapter: TypeAdapter[Any] = TypeAdapter(schema)
        raw = await self._generate(prompt, 800, adapter.json_schema())
        return adapter.validate_python(json.loads(raw))

    async def text(self, prompt: str, max_tokens: int) -> str:
        return await self._generate(prompt, max_tokens)
