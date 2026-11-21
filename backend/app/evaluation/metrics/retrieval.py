"""Standard IR metrics with binary relevance. `ranked` is the system's ordered result list;
`relevant` the set of ids judged relevant for the query."""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence


def recall_at_k(ranked: Sequence[Hashable], relevant: set[Hashable], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def precision_at_k(ranked: Sequence[Hashable], relevant: set[Hashable], k: int) -> float:
    if k <= 0:
        return 0.0
    return len(set(ranked[:k]) & relevant) / k


def hit_at_k(ranked: Sequence[Hashable], relevant: set[Hashable], k: int) -> float:
    return 1.0 if set(ranked[:k]) & relevant else 0.0


def mrr(ranked: Sequence[Hashable], relevant: set[Hashable], k: int | None = None) -> float:
    for i, item in enumerate(ranked[:k] if k else ranked, start=1):
        if item in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: Sequence[Hashable], relevant: set[Hashable], k: int) -> float:
    dcg = sum(
        1.0 / math.log2(i + 1) for i, item in enumerate(ranked[:k], start=1) if item in relevant
    )
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0
