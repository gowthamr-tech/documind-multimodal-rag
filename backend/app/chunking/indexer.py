"""Chunk + embed a document version into one strategy/embedding space (idempotent)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.chunking.base import ChunkDraft, ChunkingConfig, embedding_text
from app.chunking.source import build_blocks
from app.chunking.strategies import get_chunker
from app.database.models import Chunk, ChunkEmbedding, Document, DocumentVersion, EmbeddingSpace
from app.embeddings.providers import EmbeddingProvider, space_id_for


@dataclass(slots=True)
class IndexStats:
    chunks: int
    embedded: int
    parents: int


def is_retrievable(d: ChunkDraft) -> bool:
    return d.metadata.get("role") != "parent"


def ensure_space(session: Session, embedder: EmbeddingProvider) -> uuid.UUID:
    """Register a (non-indexed) space for experiment providers; the default space comes from a
    migration with its HNSW index. Unindexed spaces use exact search (fine at benchmark scale)."""
    sid = space_id_for(embedder.provider, embedder.model)
    if session.get(EmbeddingSpace, sid) is None:
        session.add(
            EmbeddingSpace(
                id=sid,
                provider=embedder.provider,
                model=embedder.model,
                dim=embedder.dim,
                normalized=True,
                indexed=False,
            )
        )
        session.flush()
    return sid


def index_version(
    session: Session,
    *,
    org_id: uuid.UUID,
    version_id: uuid.UUID,
    embedder: EmbeddingProvider,
    strategy: str = "parent_child",
    cfg: ChunkingConfig | None = None,
) -> IndexStats:
    title = session.execute(
        select(Document.title)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(DocumentVersion.id == version_id)
    ).scalar_one()
    blocks = build_blocks(session, version_id)
    drafts = get_chunker(strategy, cfg, embedder=embedder).chunk(blocks)
    drafts = [d for d in drafts if d.content.strip()]

    session.execute(
        delete(Chunk).where(Chunk.document_version_id == version_id, Chunk.strategy == strategy)
    )
    ids = [uuid.uuid4() for _ in drafts]
    rows = []
    for i, (cid, d) in enumerate(zip(ids, drafts, strict=True)):
        rows.append(
            {
                "id": cid,
                "org_id": org_id,
                "document_version_id": version_id,
                "strategy": strategy,
                "chunk_type": d.chunk_type.value,
                "seq": i,
                "parent_chunk_id": ids[d.parent_index] if d.parent_index is not None else None,
                "is_retrievable": is_retrievable(d),
                "content": d.content,
                "content_for_embedding": embedding_text(d, title),
                "page_start": d.page_start,
                "page_end": d.page_end,
                "bboxes": d.bboxes,
                "section_path": d.section_path,
                "element_refs": d.element_refs,
                "token_count": d.token_count,
                "metadata": d.metadata,
            }
        )
    # parents first so self-referencing FKs resolve
    rows.sort(key=lambda r: r["parent_chunk_id"] is not None)
    if rows:
        session.execute(insert(Chunk), rows)

    space = ensure_space(session, embedder)
    to_embed = [r for r in rows if r["is_retrievable"]]
    vectors = embedder.embed_documents([r["content_for_embedding"] for r in to_embed])
    if to_embed:
        session.execute(
            insert(ChunkEmbedding),
            [
                {"chunk_id": r["id"], "space_id": space, "org_id": org_id, "embedding": v}
                for r, v in zip(to_embed, vectors, strict=True)
            ],
        )
    return IndexStats(
        chunks=len(rows),
        embedded=len(to_embed),
        parents=sum(1 for r in rows if not r["is_retrievable"]),
    )
