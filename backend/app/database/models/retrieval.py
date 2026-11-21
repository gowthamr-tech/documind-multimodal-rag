"""Chunks and embeddings (Phase 4).

`chunk_embeddings.embedding` is a dimensionless pgvector column so several embedding spaces
(models) can coexist; each production space gets a partial HNSW index on
`(embedding::vector(dim)) WHERE space_id = <id>` created by a migration (index DDL needs the
owner role, which the app role deliberately is not).
"""

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, Timestamps, UUIDPrimaryKey

DEFAULT_SPACE_ID = uuid.uuid5(
    uuid.NAMESPACE_URL, "documind:embedding-space:local:sentence-transformers/all-MiniLM-L6-v2"
)


class Chunk(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_version_strategy", "org_id", "document_version_id", "strategy"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_parent", "parent_chunk_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    strategy: Mapped[str] = mapped_column(String(30), nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(20), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE")
    )
    is_retrievable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_for_embedding: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    bboxes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    section_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    element_refs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content_for_embedding)", persisted=True)
    )


class EmbeddingSpace(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "embedding_spaces"
    __table_args__ = (
        UniqueConstraint("provider", "model", name="uq_embedding_spaces_provider_model"),
    )

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    space_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("embedding_spaces.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
