"""Celery application (ADR 0006).

Queues:
  ingest_cpu — parsing/OCR/layout (CPU-bound; prefork workers, recycled to contain memory)
  vlm_io     — vision-model calls (I/O-bound; higher concurrency)          [Phase 3]
  embed      — chunking + embeddings                                        [Phase 4]
  maintenance — purges and housekeeping
"""

from typing import Any

from celery import Celery
from celery.signals import setup_logging

from app.core.config import get_settings
from app.core.logging import configure_logging


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    """Use our structured logging in workers instead of Celery's default handlers."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery("documind", broker=settings.redis_url, include=["app.workers.tasks"])
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_backend=None,  # status lives in Postgres (processing_jobs), not Celery results
        task_ignore_result=True,
        task_acks_late=True,  # a crashed worker's task is redelivered...
        task_reject_on_worker_lost=True,  # ...instead of being silently lost
        worker_prefetch_multiplier=1,  # long tasks: don't hoard work
        worker_max_tasks_per_child=50,  # recycle to cap parser memory growth
        broker_transport_options={"visibility_timeout": 3 * 3600},
        broker_connection_retry_on_startup=True,
        task_default_queue="ingest_cpu",
        task_routes={
            "documind.process_document_version": {"queue": "ingest_cpu"},
            "documind.purge_document": {"queue": "maintenance"},
        },
        task_always_eager=settings.celery_task_always_eager,
        task_eager_propagates=settings.celery_task_always_eager,
        timezone="UTC",
        worker_hijack_root_logger=False,
        worker_redirect_stdouts=False,
    )
    return app


celery_app = create_celery()
