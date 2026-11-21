# ADR 0006 — Background jobs

- **Status:** Accepted (2026-11-18)
- **Decision:** **Celery + Redis broker**, queues `ingest_cpu`, `vlm_io`, `embed`. `acks_late=True`, `task_reject_on_worker_lost=True`, `worker_max_tasks_per_child` to contain parser memory growth, exponential-backoff retries on transient errors only. Idempotency: tasks are keyed by `(document_version_id, stage, pipeline_version)`.
- **Rejected:** Arq (asyncio-first, poor fit for CPU-bound sync parsing), Dramatiq (smaller ecosystem).
