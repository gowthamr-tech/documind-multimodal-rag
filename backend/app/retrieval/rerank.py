"""Cross-encoder reranking (local, deterministic), implemented on `transformers` directly.

Default: cross-encoder/ms-marco-MiniLM-L6-v2 (~90 MB) — chosen over bge-reranker-base (~280 MB)
for this project's disk budget. If the weights are not available locally the reranker reports
itself unavailable and retrieval proceeds without it (recorded in timings/scores), never fakes it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

DEFAULT_RERANKER = "cross-encoder/ms-marco-MiniLM-L6-v2"


class Reranker(Protocol):
    def score(self, query: str, passages: list[str]) -> list[float]: ...


class RerankerUnavailableError(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _model(name: str):  # type: ignore[no-untyped-def]
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    try:
        tok = AutoTokenizer.from_pretrained(name, local_files_only=True)
        model = AutoModelForSequenceClassification.from_pretrained(
            name, local_files_only=True
        ).eval()
    except OSError as exc:
        raise RerankerUnavailableError(f"reranker weights not found locally: {name}") from exc
    return tok, model


def reranker_available(name: str = DEFAULT_RERANKER) -> bool:
    try:
        _model(name)
    except RerankerUnavailableError:
        return False
    return True


class CrossEncoderReranker:
    def __init__(self, model: str = DEFAULT_RERANKER, batch_size: int = 16) -> None:
        self.model = model
        self.batch_size = batch_size

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        import torch

        tok, model = _model(self.model)
        scores: list[float] = []
        with torch.inference_mode():
            for i in range(0, len(passages), self.batch_size):
                batch = tok(
                    [query] * len(passages[i : i + self.batch_size]),
                    passages[i : i + self.batch_size],
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                )
                logits = model(**batch).logits
                scores.extend(logits.view(-1).tolist())
        return scores
