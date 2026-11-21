"""Google Gemini adapter over the public REST API (generativelanguage.googleapis.com).

Uses httpx directly instead of the SDK to keep the dependency footprint small. Structured output
uses `responseMimeType: application/json` + `responseSchema` (OpenAPI-subset schema).

NOTE: this adapter has unit tests against recorded response shapes only. It has NOT been run
against the live API in this project yet (no key was available at build time).
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterator
from typing import Any

import httpx

from app.core.logging import get_logger
from app.providers.llm.base import (
    Message,
    ModelResult,
    ProviderError,
    ProviderResponseError,
    Usage,
)

logger = get_logger("app.providers.gemini")
_BASE = "https://generativelanguage.googleapis.com/v1beta"
_RETRYABLE = {429, 500, 502, 503, 504}


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model_id: str,
        timeout_s: float = 60.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model_id = model_id
        self._max_retries = max_retries
        self._http = httpx.Client(
            base_url=_BASE,
            timeout=timeout_s,
            headers={"x-goog-api-key": api_key},
            transport=transport,
        )

    # -- core ---------------------------------------------------------------------------
    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        delay = 1.0
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._http.post(path, json=body)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise ProviderError(f"Gemini transport error: {exc}") from exc
            else:
                if resp.status_code < 400:
                    data: dict[str, Any] = resp.json()
                    return data
                if resp.status_code not in _RETRYABLE or attempt == self._max_retries:
                    raise ProviderError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
            logger.warning("gemini_retry", attempt=attempt + 1, delay_s=delay)
            time.sleep(delay)
            delay = min(delay * 2, 20)
        raise ProviderError("unreachable")  # pragma: no cover

    @staticmethod
    def _parse(data: dict[str, Any], model: str) -> ModelResult:
        try:
            cand = data["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"])
        except (KeyError, IndexError, TypeError) as exc:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            raise ProviderResponseError(
                f"Gemini returned no content (block reason: {reason})"
            ) from exc
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderResponseError("Gemini output was not valid JSON") from exc
        meta = data.get("usageMetadata") or {}
        usage = Usage(
            int(meta.get("promptTokenCount", 0)), int(meta.get("candidatesTokenCount", 0))
        )
        return ModelResult(data=parsed, model=model, usage=usage, raw_text=text)

    @staticmethod
    def _contents(messages: list[Message]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in messages
            if m.role != "system"
        ]
        return ({"parts": [{"text": system}]} if system else None), contents

    # -- VisionModel --------------------------------------------------------------------
    def generate_json(self, **kwargs: Any) -> ModelResult:
        if "image_png" in kwargs:
            return self._vision_json(kwargs["image_png"], kwargs["prompt"], kwargs["schema"])
        return self._chat_json(kwargs["messages"], kwargs["schema"], kwargs.get("temperature", 0.0))

    def _vision_json(self, image_png: bytes, prompt: str, schema: dict[str, Any]) -> ModelResult:
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": base64.b64encode(image_png).decode(),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        return self._parse(
            self._post(f"/models/{self.model_id}:generateContent", body), self.model_id
        )

    # -- ChatModel ----------------------------------------------------------------------
    def _chat_json(
        self, messages: list[Message], schema: dict[str, Any], temperature: float
    ) -> ModelResult:
        system, contents = self._contents(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        if system:
            body["systemInstruction"] = system
        return self._parse(
            self._post(f"/models/{self.model_id}:generateContent", body), self.model_id
        )

    def stream_text(self, *, messages: list[Message], temperature: float = 0.2) -> Iterator[str]:
        system, contents = self._contents(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if system:
            body["systemInstruction"] = system
        with self._http.stream(
            "POST",
            f"/models/{self.model_id}:streamGenerateContent",
            params={"alt": "sse"},
            json=body,
        ) as resp:
            if resp.status_code >= 400:
                raise ProviderError(f"Gemini HTTP {resp.status_code}")
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = json.loads(line[5:].strip())
                for cand in chunk.get("candidates", []):
                    for part in (cand.get("content") or {}).get("parts", []):
                        if part.get("text"):
                            yield part["text"]
