# Database Design (PostgreSQL 16 + pgvector)

Status: approved design. Tables are created incrementally by Alembic migrations. The **Phase** column
shows when each table lands.

## Conventions

- UUID primary keys (`gen_random_uuid()`), except `audit_logs` (BIGSERIAL, for write throughput).
- `created_at` / `updated_at` are `timestamptz`, set by default to `now()`.
- Every tenant-scoped table has `org_id` and an RLS policy:
  `USING (org_id = current_setting('app.org_id')::uuid)`. The API sets `SET LOCAL app.org_id` per transaction.
  The application role is **not** the table owner, so RLS cannot be bypassed.
- Bounding boxes are stored as `[x0, y0, x1, y1]`, normalised to 0–1 by page width/height, origin top-left.
- Soft delete (`deleted_at`) for documents. A background purge removes blobs and derived rows.

## Tables

### Identity & governance (Phase 2)

| Table | Key columns | Notes |
|---|---|---|
| `organizations` | id, name, slug UNIQUE | |
| `users` | id, email UNIQUE (citext), password_hash, is_active | argon2id |
| `memberships` | org_id, user_id, role ∈ {owner, admin, member, viewer} | PK(org_id, user_id) |
| `refresh_tokens` | id, user_id, token_hash, family_id, expires_at, revoked_at | reuse of a revoked token revokes the whole family |
| `audit_logs` | id, org_id, actor_id, action, resource_type, resource_id, ip, user_agent, metadata JSONB | append-only: app role has INSERT/SELECT only |
| `approvals` | id, org_id, thread_id, requested_by, action, payload JSONB, status, decided_by, decided_at | Phase 5 (HITL) |

### Documents & processing (Phase 2–3)

| Table | Key columns | Notes |
|---|---|---|
| `documents` | id, org_id, title, current_version_id, classification, tags TEXT[], sensitivity ∈ {normal, confidential}, created_by, deleted_at | |
| `document_versions` | id, document_id, org_id, version_no, sha256, mime_type, size_bytes, storage_key, original_filename, page_count, metadata JSONB, status, pipeline_version | `UNIQUE(org_id, sha256)` gives idempotent dedupe |
| `processing_jobs` | id, org_id, document_version_id, celery_task_id, stage, status, attempt, error JSONB, started_at, finished_at, metrics JSONB | metrics: latency, pages/s, tokens, cost |
| `pages` | id, document_version_id, page_no, width, height, rotation, render_key, has_text_layer, ocr_applied, ocr_mean_conf, sheet_name | XLSX: one row per sheet |

### Extracted structure (Phase 3)

| Table | Key columns | Notes |
|---|---|---|
| `elements` | id, document_version_id, page_id, type, bbox, reading_order, text, section_path TEXT[], parent_element_id, attributes JSONB | type ∈ title, section_header, paragraph, list, table, figure, chart, caption, footnote, header, footer, formula |
| `tables` | id, element_id, document_version_id, page_start, page_end, caption, header_depth, units, footnotes JSONB, structure JSONB, markdown, html, extractor, extractor_version | |
| `table_cells` | id, table_id, row_idx, col_idx, row_span, col_span, is_header, header_path TEXT[], raw_text, numeric_value NUMERIC, unit, scale, bbox | used by the Table Agent for exact lookups |
| `charts` | id, element_id, document_version_id, chart_type, title, x_axis JSONB, y_axis JSONB, legend JSONB, series JSONB, trends JSONB, values_are_estimated BOOL, model, prompt_version, validation JSONB | |
| `images` | id, element_id, crop_key, description, model, prompt_version | |

### Retrieval (Phase 4)

| Table | Key columns | Notes |
|---|---|---|
| `chunks` | id, org_id, document_version_id, strategy, chunk_type, parent_chunk_id, content, content_for_embedding, page_start, page_end, bboxes JSONB, section_path TEXT[], element_ids UUID[], token_count, `tsv` generated tsvector | GIN(tsv); btree(org_id, document_version_id, strategy) |
| `embedding_spaces` | id, provider, model, dim, normalized | registry, so several models can coexist |
| `chunk_embeddings` | chunk_id, space_id, embedding VECTOR | PK(chunk_id, space_id); per-space **partial HNSW** index on `(embedding::vector(dim)) WHERE space_id = X` |

Filtered ANN loses recall, so `hnsw.iterative_scan = relaxed_order` is enabled and recall is measured
against exact search (Phase 4).

### Conversations & answers (Phase 5)

| Table | Key columns |
|---|---|
| `conversations` | id, org_id, user_id, title, document_scope UUID[] |
| `messages` | id, conversation_id, role, content |
| `query_runs` | id, org_id, message_id, graph_thread_id, route JSONB, agent_trace JSONB, prompt_tokens, completion_tokens, cost_usd, latency_ms, status, error |
| `citations` | id, message_id, claim_idx, chunk_id, document_version_id, page_no, bbox, quote, verification ∈ {verified, partial, unsupported}, verifier_detail JSONB |

LangGraph checkpoint tables are managed by `langgraph-checkpoint-postgres`.

### Evaluation (Phase 7)

| Table | Key columns |
|---|---|
| `eval_datasets` | id, name, version, sha256, description, license_notes |
| `eval_items` | id, dataset_id, kind ∈ {qa, retrieval, table, chart, ocr}, input JSONB, expected JSONB, verified_by, verified_at |
| `eval_runs` | id, dataset_id, config JSONB, git_sha, started_at, finished_at, summary JSONB |
| `eval_results` | id, run_id, item_id, metrics JSONB, output JSONB |

## Versioning semantics

Uploading with an existing `document_id` creates `document_versions.version_no + 1` and moves
`documents.current_version_id`. Old versions keep their chunks and citations, so historical answers
remain verifiable against the exact bytes they cited.
