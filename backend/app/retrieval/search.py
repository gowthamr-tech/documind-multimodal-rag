"""Hybrid retrieval over chunks: dense (pgvector) + lexical (PostgreSQL FTS) -> RRF -> rerank.

Scope: only *current* versions of non-deleted documents, inside the caller's org (RLS), and only
retrievable chunks of the chosen chunking strategy. Lexical ranking is `ts_rank_cd` over an OR
query of the question's lexemes — honest name: "lexical", not BM25 (ADR 0005).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class Filters:
    document_ids: list[uuid.UUID] | None = None
    chunk_types: list[str] | None = None
    page_min: int | None = None
    page_max: int | None = None
    strategy: str = "parent_child"


@dataclass(slots=True)
class Space:
    id: uuid.UUID
    dim: int
    indexed: bool


@dataclass(slots=True)
class Hit:
    chunk_id: uuid.UUID
    score: float
    rank: int


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    version_id: uuid.UUID
    chunk_type: str
    content: str
    page_start: int | None
    page_end: int | None
    bboxes: list[dict[str, Any]]
    section_path: list[str]
    parent_chunk_id: uuid.UUID | None
    metadata: dict[str, Any]
    scores: dict[str, float] = field(default_factory=dict)
    parent_content: str | None = None
    parent_type: str | None = None


_SCOPE = """
    JOIN documents d ON d.current_version_id = c.document_version_id AND d.deleted_at IS NULL
    WHERE c.is_retrievable AND c.strategy = :strategy
      AND (CAST(:doc_ids AS uuid[]) IS NULL OR d.id = ANY(CAST(:doc_ids AS uuid[])))
      AND (CAST(:types AS text[]) IS NULL OR c.chunk_type = ANY(CAST(:types AS text[])))
      AND (CAST(:pmin AS int) IS NULL OR c.page_end >= CAST(:pmin AS int))
      AND (CAST(:pmax AS int) IS NULL OR c.page_start <= CAST(:pmax AS int))
"""


def _params(f: Filters) -> dict[str, Any]:
    return {
        "strategy": f.strategy,
        "doc_ids": [str(d) for d in f.document_ids] if f.document_ids else None,
        "types": f.chunk_types or None,
        "pmin": f.page_min,
        "pmax": f.page_max,
    }


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


async def dense_search(
    session: AsyncSession, query_vec: list[float], space: Space, f: Filters, k: int
) -> list[Hit]:
    dim = int(space.dim)  # interpolated as an int literal: the expression must match the index
    expr = f"(e.embedding::vector({dim}))"
    if space.indexed:
        # Filtered HNSW: keep scanning until k rows survive the filters (pgvector >= 0.8).
        await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        await session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    sql = f"""
        SELECT c.id, 1 - ({expr} <=> CAST(:q AS vector({dim}))) AS score
        FROM chunk_embeddings e
        JOIN chunks c ON c.id = e.chunk_id
        {_SCOPE} AND e.space_id = :space
        ORDER BY {expr} <=> CAST(:q AS vector({dim}))
        LIMIT :k
    """  # noqa: S608 - only int dim and static fragments are interpolated
    rows = (
        await session.execute(
            text(sql), {**_params(f), "q": _vec(query_vec), "space": space.id, "k": k}
        )
    ).all()
    return [Hit(r[0], float(r[1]), i + 1) for i, r in enumerate(rows)]


async def lexical_search(session: AsyncSession, query: str, f: Filters, k: int) -> list[Hit]:
    sql = f"""
        WITH q AS (
          SELECT NULLIF(replace(plainto_tsquery('english', :query)::text, '&', '|'), '')::tsquery AS tsq
        )
        SELECT c.id, ts_rank_cd(c.tsv, q.tsq, 32) AS score
        FROM chunks c CROSS JOIN q
        {_SCOPE} AND q.tsq IS NOT NULL AND c.tsv @@ q.tsq
        ORDER BY score DESC, c.id
        LIMIT :k
    """  # noqa: S608 - static fragments only
    rows = (await session.execute(text(sql), {**_params(f), "query": query, "k": k})).all()
    return [Hit(r[0], float(r[1]), i + 1) for i, r in enumerate(rows)]


def rrf(*ranked: list[Hit], k: int = 60) -> list[tuple[uuid.UUID, float]]:
    """Reciprocal Rank Fusion (Cormack et al. 2009): sum(1 / (k + rank))."""
    scores: dict[uuid.UUID, float] = {}
    for hits in ranked:
        for h in hits:
            scores[h.chunk_id] = scores.get(h.chunk_id, 0.0) + 1.0 / (k + h.rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], str(kv[0])))


async def load_chunks(
    session: AsyncSession, ids: list[uuid.UUID]
) -> dict[uuid.UUID, RetrievedChunk]:
    if not ids:
        return {}
    rows = (
        await session.execute(
            text("""
                SELECT c.id, d.id, d.title, c.document_version_id, c.chunk_type, c.content,
                       c.page_start, c.page_end, c.bboxes, c.section_path, c.parent_chunk_id,
                       c.metadata, p.content, p.chunk_type
                FROM chunks c
                JOIN documents d ON d.current_version_id = c.document_version_id
                LEFT JOIN chunks p ON p.id = c.parent_chunk_id
                WHERE c.id = ANY(CAST(:ids AS uuid[]))
            """),
            {"ids": [str(i) for i in ids]},
        )
    ).all()
    return {
        r[0]: RetrievedChunk(
            chunk_id=r[0],
            document_id=r[1],
            document_title=r[2],
            version_id=r[3],
            chunk_type=r[4],
            content=r[5],
            page_start=r[6],
            page_end=r[7],
            bboxes=r[8] or [],
            section_path=list(r[9] or []),
            parent_chunk_id=r[10],
            metadata=r[11] or {},
            parent_content=r[12],
            parent_type=r[13],
        )
        for r in rows
    }
