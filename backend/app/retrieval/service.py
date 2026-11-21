"""Process-wide retriever wiring from settings."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.embeddings.providers import get_embedder
from app.retrieval.rerank import CrossEncoderReranker, reranker_available
from app.retrieval.retriever import Retriever


@lru_cache(maxsize=1)
def get_retriever() -> Retriever:
    s = get_settings()
    return Retriever(
        get_embedder(s.embedding_provider, s.embedding_model),
        CrossEncoderReranker(s.reranker_model)
        if s.rerank_enabled and reranker_available(s.reranker_model)
        else None,
    )
