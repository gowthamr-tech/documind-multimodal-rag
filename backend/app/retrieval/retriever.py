"""The retrieval pipeline and context assembly.

    query -> [expansion] -> dense + lexical (per query) -> RRF -> [cross-encoder rerank]
          -> parent expansion + dedupe -> per-document balancing -> token-budgeted context

Context items get server-assigned source IDs (S1, S2, ...). The generator may only cite those
IDs; `resolve_citation` maps an ID back to document/page/bbox/section/quote. Citation
provenance never comes from model output.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.chunking.base import count_tokens
from app.database.models import EmbeddingSpace
from app.embeddings.providers import EmbeddingProvider, space_id_for
from app.retrieval.rerank import Reranker
from app.retrieval.search import (
    Filters,
    RetrievedChunk,
    Space,
    dense_search,
    lexical_search,
    load_chunks,
    rrf,
)


@dataclass(slots=True)
class RetrievalConfig:
    mode: str = "hybrid"  # dense | lexical | hybrid
    k_dense: int = 30
    k_lexical: int = 30
    top_k: int = 8
    rerank: bool = True
    rerank_candidates: int = 24
    expand_parents: bool = True
    per_document_min: int = 2  # comparison: guarantee each selected doc some evidence


@dataclass(slots=True)
class ContextItem:
    source_id: str
    chunk: RetrievedChunk
    text: str  # what the LLM sees (parent content when expanded)


@dataclass(slots=True)
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk]
    timings_ms: dict[str, float] = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)


class Retriever:
    def __init__(self, embedder: EmbeddingProvider, reranker: Reranker | None = None) -> None:
        self.embedder = embedder
        self.reranker = reranker
        self._space: Space | None = None

    async def space(self, session: AsyncSession) -> Space:
        if self._space is None:
            sid = space_id_for(self.embedder.provider, self.embedder.model)
            row = (
                await session.execute(select(EmbeddingSpace).where(EmbeddingSpace.id == sid))
            ).scalar_one_or_none()
            if row is None:
                raise LookupError(
                    f"embedding space not registered: {self.embedder.provider}:{self.embedder.model}"
                )
            self._space = Space(row.id, row.dim, row.indexed)
        return self._space

    async def search(
        self,
        session: AsyncSession,
        query: str,
        filters: Filters | None = None,
        cfg: RetrievalConfig | None = None,
        extra_queries: list[str] | None = None,
    ) -> RetrievalResult:
        filters = filters or Filters()
        cfg = cfg or RetrievalConfig()
        timings: dict[str, float] = {}
        queries = [query, *(extra_queries or [])]
        ranked_lists = []

        t = time.perf_counter()
        if cfg.mode in {"dense", "hybrid"}:
            space = await self.space(session)
            vecs = await run_in_threadpool(lambda: [self.embedder.embed_query(q) for q in queries])
            timings["embed_ms"] = _ms(t)
            t = time.perf_counter()
            for v in vecs:
                ranked_lists.append(await dense_search(session, v, space, filters, cfg.k_dense))
            timings["dense_ms"] = _ms(t)
        if cfg.mode in {"lexical", "hybrid"}:
            t = time.perf_counter()
            for q in queries:
                ranked_lists.append(await lexical_search(session, q, filters, cfg.k_lexical))
            timings["lexical_ms"] = _ms(t)

        fused = (
            rrf(*ranked_lists)
            if len(ranked_lists) > 1
            else [(h.chunk_id, h.score) for h in (ranked_lists[0] if ranked_lists else [])]
        )
        candidate_ids = [cid for cid, _ in fused[: max(cfg.rerank_candidates, cfg.top_k)]]
        chunks = await load_chunks(session, candidate_ids)
        ordered = [chunks[c] for c in candidate_ids if c in chunks]
        for c, (_, s) in zip(ordered, fused, strict=False):
            c.scores["fused"] = s

        if cfg.rerank and self.reranker is not None and ordered:
            t = time.perf_counter()
            scores = await run_in_threadpool(
                self.reranker.score, query, [_rerank_text(c) for c in ordered]
            )
            for c, s in zip(ordered, scores, strict=True):
                c.scores["rerank"] = s
            ordered.sort(key=lambda c: -c.scores["rerank"])
            timings["rerank_ms"] = _ms(t)

        selected = _balance(ordered, filters.document_ids, cfg.top_k, cfg.per_document_min)
        return RetrievalResult(query=query, chunks=selected, timings_ms=timings, queries=queries)


def _rerank_text(c: RetrievedChunk) -> str:
    head = " > ".join([c.document_title, *c.section_path])
    return f"{head}\n{c.content}"


def _balance(
    chunks: list[RetrievedChunk], doc_ids: list[uuid.UUID] | None, top_k: int, per_doc_min: int
) -> list[RetrievedChunk]:
    """For multi-document questions, guarantee each requested document its best `per_doc_min`
    chunks, then fill up to `top_k` by score. Output keeps score order."""
    if not doc_ids or len(doc_ids) < 2:
        return chunks[:top_k]
    picked: dict[uuid.UUID, RetrievedChunk] = {}
    for d in doc_ids:
        for c in [c for c in chunks if c.document_id == d][:per_doc_min]:
            picked[c.chunk_id] = c
    for c in chunks:
        if len(picked) >= top_k:
            break
        picked.setdefault(c.chunk_id, c)
    rank = {c.chunk_id: i for i, c in enumerate(chunks)}
    return sorted(picked.values(), key=lambda c: rank[c.chunk_id])


def assemble_context(
    chunks: list[RetrievedChunk], *, token_budget: int = 3500, expand_parents: bool = True
) -> list[ContextItem]:
    """Number sources S1..Sn; expand children to their parent once; respect the token budget."""
    items: list[ContextItem] = []
    used_parents: set[uuid.UUID] = set()
    used = 0
    for c in chunks:
        body = c.content
        if expand_parents and c.parent_chunk_id and c.parent_content:
            if c.parent_chunk_id in used_parents:
                continue
            used_parents.add(c.parent_chunk_id)
            body = c.parent_content
        cost = count_tokens(body)
        if items and used + cost > token_budget:
            # Prefer the precise child over dropping the evidence entirely.
            body, cost = c.content, count_tokens(c.content)
            if used + cost > token_budget:
                continue
        items.append(ContextItem(source_id=f"S{len(items) + 1}", chunk=c, text=body))
        used += cost
    return items


def format_context(items: list[ContextItem]) -> str:
    blocks = []
    for it in items:
        c = it.chunk
        where = f"p. {c.page_start}" if c.page_start else "page n/a"
        if c.page_end and c.page_end != c.page_start:
            where = f"pp. {c.page_start}-{c.page_end}"
        section = " > ".join(c.section_path) or "-"
        flag = (
            " [chart values may be estimates]"
            if c.chunk_type == "chart" and c.metadata.get("values_are_estimated", True)
            else ""
        )
        blocks.append(
            f"[{it.source_id}] {c.document_title} | {where} | {section} | {c.chunk_type}{flag}\n{it.text}"
        )
    return "\n\n".join(blocks)


def resolve_citation(item: ContextItem, quote: str | None = None) -> dict[str, Any]:
    """Server-side provenance for a source ID. `quote` is kept only if it appears verbatim."""
    c = item.chunk
    bbox = c.bboxes[0] if c.bboxes else None
    verbatim = quote if quote and _norm(quote) in _norm(item.text) else None
    return {
        "citation_id": item.source_id,
        "chunk_id": str(c.chunk_id),
        "document_id": str(c.document_id),
        "version_id": str(c.version_id),
        "document_title": c.document_title,
        "page": c.page_start,
        "page_end": c.page_end,
        "section": c.section_path,
        "bbox": bbox["bbox"] if bbox else None,
        "bbox_page": bbox["page"] if bbox else None,
        "chunk_type": c.chunk_type,
        "quote": verbatim or _snippet(item.text),
        "quote_is_verbatim": verbatim is not None,
    }


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _snippet(s: str, n: int = 280) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _ms(t: float) -> float:
    return round((time.perf_counter() - t) * 1000, 2)
