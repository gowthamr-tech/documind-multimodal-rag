"""Celery tasks. Thin wrappers: all logic lives in app.ingestion / app.services so it can be
tested without a broker."""

import uuid

from celery import Task
from sqlalchemy import delete

from app.core.logging import get_logger
from app.database.models import Document
from app.database.session import sync_session_scope
from app.ingestion.pipeline import run_pipeline
from app.storage import get_storage
from app.workers.celery_app import celery_app

logger = get_logger("app.tasks")


@celery_app.task(
    name="documind.process_document_version",
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError),  # transient infrastructure errors only
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def process_document_version(self: Task, version_id: str, org_id: str) -> str | None:
    status = run_pipeline(
        uuid.UUID(version_id), uuid.UUID(org_id), attempt=self.request.retries + 1
    )
    return status.value if status else None


@celery_app.task(
    name="documind.purge_document", max_retries=5, autoretry_for=(Exception,), retry_backoff=True
)
def purge_document(document_id: str, org_id: str) -> int:
    """Hard-delete a soft-deleted document: blobs first, then rows (cascade)."""
    org = uuid.UUID(org_id)
    deleted = get_storage().delete_prefix(f"org/{org}/docs/{document_id}/")
    with sync_session_scope(org) as s:
        s.execute(
            delete(Document).where(
                Document.id == uuid.UUID(document_id), Document.deleted_at.is_not(None)
            )
        )
    logger.info("document_purged", document_id=document_id, objects=deleted)
    return deleted
