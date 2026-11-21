"""Embedding providers (ADR 0005/0009).

  local   transformers, default sentence-transformers/all-MiniLM-L6-v2 (384-d). No key; CPU.
  gemini  REST embedContent/batchEmbedContents.        [unit-tested with mocks; not run live]
  openai  REST /v1/embeddings.                          [unit-tested with mocks; not run live]

All vectors are L2-normalised so cosine distance == 1 - dot product in pgvector.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import httpx

from app.providers.llm.base import ProviderError

# BGE models recommend this instruction prefix for *queries* (not passages).
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


_LOCAL_DIMS = {
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
}


class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def space_id_for(provider: str, model: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"documind:embedding-space:{provider}:{model}")


def _normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


@lru_cache(maxsize=4)
def _hf(name: str):  # type: ignore[no-untyped-def]
    import torch
    from transformers import AutoModel, AutoTokenizer

    torch.set_num_threads(max(1, (torch.get_num_threads() or 2)))
    tok = AutoTokenizer.from_pretrained(name)
    model = AutoModel.from_pretrained(name).eval()
    return tok, model


@dataclass
class LocalEmbeddings:
    """Local transformer embeddings via `transformers` (no sentence-transformers dependency).

    Pooling follows each model family's published recipe: CLS for BGE, attention-masked mean
    for sentence-transformers MiniLM/MPNet. Vectors are L2-normalised."""

    model: str = "sentence-transformers/all-MiniLM-L6-v2"
    dim: int = 384
    batch_size: int = 32
    max_length: int = 256
    provider: str = "local"

    def _encode(self, texts: list[str]) -> list[list[float]]:
        import torch

        tok, model = _hf(self.model)
        out: list[list[float]] = []
        cls_pool = "bge" in self.model.lower()
        with torch.inference_mode():
            for i in range(0, len(texts), self.batch_size):
                batch = tok(
                    texts[i : i + self.batch_size],
                    padding=True,
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors="pt",
                )
                hidden = model(**batch).last_hidden_state
                if cls_pool:
                    pooled = hidden[:, 0]
                else:
                    mask = batch["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                    pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
                out.extend(pooled.tolist())
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts) if texts else []

    def embed_query(self, text: str) -> list[float]:
        prefix = _BGE_QUERY_PREFIX if "bge" in self.model.lower() else ""
        return self._encode([prefix + text])[0]


class GeminiEmbeddings:
    provider = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-embedding-001",
        dim: int = 768,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model, self.dim = model, dim
        self._http = httpx.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout=60,
            headers={"x-goog-api-key": api_key},
            transport=transport,
        )

    def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 100):
            batch = texts[i : i + 100]
            body = {
                "requests": [
                    {
                        "model": f"models/{self.model}",
                        "content": {"parts": [{"text": t}]},
                        "taskType": task,
                        "outputDimensionality": self.dim,
                    }
                    for t in batch
                ]
            }
            resp = self._http.post(f"/models/{self.model}:batchEmbedContents", json=body)
            if resp.status_code >= 400:
                raise ProviderError(f"Gemini embeddings HTTP {resp.status_code}: {resp.text[:200]}")
            out.extend(_normalise(e["values"]) for e in resp.json()["embeddings"])
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


class OpenAIEmbeddings:
    provider = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        dim: int = 1536,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model, self.dim = model, dim
        self._http = httpx.Client(
            base_url="https://api.openai.com/v1",
            timeout=60,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 256):
            body: dict[str, Any] = {
                "model": self.model,
                "input": texts[i : i + 256],
                "dimensions": self.dim,
            }
            resp = self._http.post("/embeddings", json=body)
            if resp.status_code >= 400:
                raise ProviderError(f"OpenAI embeddings HTTP {resp.status_code}: {resp.text[:200]}")
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            out.extend(_normalise(d["embedding"]) for d in data)
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def get_embedder(provider: str = "local", model: str | None = None) -> EmbeddingProvider:
    from app.core.config import get_settings

    settings = get_settings()
    if provider == "local":
        name = model or "sentence-transformers/all-MiniLM-L6-v2"
        return LocalEmbeddings(model=name, dim=_LOCAL_DIMS.get(name, 384))
    if provider == "gemini":
        if settings.gemini_api_key is None:
            raise ProviderError("GEMINI_API_KEY is not set")
        return GeminiEmbeddings(
            settings.gemini_api_key.get_secret_value(), model or "gemini-embedding-001"
        )
    if provider == "openai":
        import os

        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderError("OPENAI_API_KEY is not set")
        return OpenAIEmbeddings(key, model or "text-embedding-3-small")
    raise ValueError(f"unknown embedding provider: {provider}")
