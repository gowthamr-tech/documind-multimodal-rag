"""Alembic environment. Migrations run as the owner role (DOCUMIND_MIGRATION_DATABASE_URL)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app.database.models  # noqa: F401  (registers all tables on Base.metadata)
from app.core.config import get_settings
from app.database.base import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Per-embedding-space HNSW expression indexes are created explicitly in migrations
# (see app/database/models/retrieval.py); SQLAlchemy can't model them, so autogenerate skips them.
MANAGED_INDEX_PREFIX = "ix_chunk_embeddings_hnsw_"


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    return not (type_ == "index" and name and name.startswith(MANAGED_INDEX_PREFIX))


def _url() -> str:
    # Tests (and tooling) may inject a URL explicitly; otherwise use settings.
    return str(config.attributes.get("url") or get_settings().migration_database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:
        context.configure(
            connection=connectable,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
