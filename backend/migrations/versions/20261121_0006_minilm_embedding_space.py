"""Register the local MiniLM embedding space (default) with its partial HNSW index.

The BGE space from 0004 stays registered; its weights could not be downloaded on the build
machine's network, so the default moved to all-MiniLM-L6-v2 (already cached locally).

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SPACE_ID = "d7eb22c8-6e94-5a88-a958-5f8185f1f730"


def upgrade() -> None:
    op.execute(
        "INSERT INTO embedding_spaces (id, provider, model, dim, normalized, indexed) VALUES "
        f"('{SPACE_ID}', 'local', 'sentence-transformers/all-MiniLM-L6-v2', 384, true, true) "
        "ON CONFLICT (id) DO UPDATE SET indexed = true"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunk_embeddings_hnsw_minilm ON chunk_embeddings "
        "USING hnsw ((embedding::vector(384)) vector_cosine_ops) "
        f"WHERE space_id = '{SPACE_ID}'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunk_embeddings_hnsw_minilm")
    op.execute(f"DELETE FROM embedding_spaces WHERE id = '{SPACE_ID}'")
