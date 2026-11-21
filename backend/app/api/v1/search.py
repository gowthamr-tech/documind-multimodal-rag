import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import PrincipalDep, TenantSessionDep
from app.core.rate_limit import rate_limit
from app.retrieval.retriever import RetrievalConfig, assemble_context, resolve_citation
from app.retrieval.search import Filters
from app.retrieval.service import get_retriever

router = APIRouter(tags=["search"])


class SearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)
    chunk_types: (
        list[Literal["text", "section", "page", "table", "table_rows", "chart", "image"]] | None
    ) = None
    page_min: int | None = Field(default=None, ge=1)
    page_max: int | None = Field(default=None, ge=1)
    mode: Literal["dense", "lexical", "hybrid"] = "hybrid"
    rerank: bool = True
    top_k: int = Field(default=8, ge=1, le=50)


class SearchHit(BaseModel):
    citation: dict[str, Any]
    content: str
    context: str
    scores: dict[str, float]


class SearchOut(BaseModel):
    query: str
    hits: list[SearchHit]
    timings_ms: dict[str, float]


@router.post(
    "/search",
    response_model=SearchOut,
    dependencies=[Depends(rate_limit("query", "query_rate_limit_per_minute"))],
)
async def search(body: SearchIn, _: PrincipalDep, session: TenantSessionDep) -> SearchOut:
    """Hybrid retrieval with provenance — the evidence layer under /query."""
    retriever = get_retriever()
    result = await retriever.search(
        session,
        body.query,
        Filters(
            document_ids=body.document_ids,
            chunk_types=list(body.chunk_types or []) or None,
            page_min=body.page_min,
            page_max=body.page_max,
        ),
        RetrievalConfig(mode=body.mode, rerank=body.rerank, top_k=body.top_k),
    )
    items = assemble_context(result.chunks, token_budget=100_000)
    return SearchOut(
        query=body.query,
        hits=[
            SearchHit(
                citation=resolve_citation(i),
                content=i.chunk.content,
                context=i.text,
                scores=i.chunk.scores,
            )
            for i in items
        ],
        timings_ms=result.timings_ms,
    )
