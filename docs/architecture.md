# DocuMind AI — Architecture

Status: **Approved 2026-11-18** (Phase 1). Changes require a new ADR in `docs/adr/`.

## 1. Purpose

DocuMind AI understands, extracts, retrieves, compares and reasons over complex enterprise documents
(PDF, scanned PDF, PNG/JPEG, DOCX, XLSX, PPTX). Every factual answer is backed by server-resolved,
page-level citations and a verification status. When the evidence is insufficient, the answer says so.

## 2. Planes

| Plane | Responsibility | Runtime |
|---|---|---|
| Ingestion | validate → store original → classify → parse → extract → Document Graph → chunk → embed | Celery workers (`ingest_cpu`, `vlm_io`, `embed` queues) |
| Knowledge | Immutable originals, versioned derived artifacts, chunks with provenance | PostgreSQL 16 + pgvector + tsvector, object storage (S3 / local) |
| Reasoning | Supervisor-routed LangGraph agents, deterministic computation, verification | FastAPI process (in-process graph), Postgres checkpointer |
| Experience | Library, viewer, streaming chat, comparison, evaluation | Next.js (App Router), separate `documind-frontend` repo (ADR 0011) |

The planes communicate only through the database, object storage and the job queue.

## 3. System diagram

```
                         ┌──────────────────────────────┐
                         │  Next.js (App Router, TS)     │
                         └──────────────┬───────────────┘
                                        │ HTTPS (JWT) · SSE
                         ┌──────────────▼───────────────┐
                         │ FastAPI  /api/v1              │
                         │ auth · RBAC · rate limit ·    │
                         │ audit · problem+json errors   │
                         └──┬─────────┬──────────┬──────┘
             enqueue jobs   │         │ query     │ signed URLs
                 ┌──────────▼──┐  ┌───▼────────┐ │
                 │ Redis        │  │ LangGraph  │ │
                 │ broker·cache │  │ runtime    │ │
                 └──────┬──────┘  └──┬──────┬──┘ │
          ┌─────────────▼─────────┐  │      │    │
          │ Celery workers         │  │      │    │
          └───┬──────────────┬────┘  │      │    │
   ┌──────────▼───┐   ┌──────▼───────▼──┐ ┌─▼────▼──────────────┐
   │ Object store │   │ PostgreSQL 16    │ │ Model providers     │
   │ (S3 / local) │   │ pgvector · FTS · │ │ LLM · VLM · embed · │
   │              │   │ RLS · checkpoints│ │ rerank (abstracted) │
   └──────────────┘   └──────────────────┘ └─────────────────────┘
```

## 4. Non-negotiable design rules

1. **Citations are server-resolved.** The LLM may only reference `chunk_id`s present in its context.
   The server maps IDs → document version, page, bbox and quote. Unknown IDs are dropped and the claim
   is marked `unsupported`.
2. **Arithmetic is code.** Operands are extracted with citations. A `Decimal` calculator normalises
   unit/scale/currency and computes the result.
3. **Derived data is reproducible.** Artifacts are keyed by `(document_version_id, pipeline_version)`.
   Re-processing is idempotent and replaces artifacts atomically.
4. **Tenant isolation is enforced twice:** application filters plus PostgreSQL Row-Level Security
   (`SET LOCAL app.org_id`).
5. **Document content is untrusted input.** Agent tools are read-only. Injected instructions cannot trigger actions.
6. **No uncalibrated confidence numbers.** Verification status is categorical until calibration is
   measured (Phase 7).

## 5. Multimodal processing pipeline

```
upload ─► validate (size, magic bytes, OOXML zip-bomb ratio, encrypted-PDF check)
       ─► sha256 → dedupe ─► store original (immutable) ─► enqueue(version_id)

ingest_cpu: 1 classify (native | scanned | mixed, per-page text-layer density)
            2 render (pypdfium2; DOCX/PPTX → LibreOffice → PDF for display/page anchors)
            3 parse  (Docling layout + reading order + section hierarchy; Tesseract OCR only where needed)
            4 tables (TableFormer → spans, header_path, units/scale, numeric values, multi-page stitching)
            5 figures (crop → picture classifier → chart | image)
            6 metadata (headers/footers detected and excluded from body text)
            → persist Document Graph in one transaction
vlm_io:     7 charts (VLM → ChartSchema JSON → validated against OCR'd ticks; values flagged as estimates)
            8 images (VLM descriptions)
embed:      9 chunk (configured strategy)  10 embed (provider → embedding space) → status=ready
```

Tools and the reasons for choosing them are recorded in ADRs 0002–0004.

## 6. Retrieval

```
query → optional expansion (multi-query, HyDE)
      → dense top-k ∪ lexical top-k (metadata filters; RLS scopes the org)
      → Reciprocal Rank Fusion → cross-encoder rerank → parent expansion + dedupe
      → context assembly (token budget, per-document balancing, tables as markdown with units,
        chart chunks tagged "values estimated")
```

Lexical ranking uses PostgreSQL `ts_rank_cd`, which is **not BM25**. It is benchmarked against `bm25s`
(ADR 0005).

## 7. Agent workflow (LangGraph)

```
Supervisor (structured intent: lookup | table_numeric | chart | compare | summarize | out_of_scope)
  → [Doc Classification]  only when scope is ambiguous
  → Retrieval             per-document fan-out (Send) for comparisons
  → [Table Agent] [Chart Agent]   only when retrieved evidence contains tables/charts
  → Reasoning             plan → cited operands → calculator → draft claims
  → Verification          ID resolution · quote/number match · entailment · recompute
        ├─ fail & attempts < 2 → Retrieval (refined query)
        └─ pass / exhausted   → Response (or "insufficient evidence")
interrupt(): confidential docs for non-admins, bulk export/report saving, any future write tool
```

Limits: `recursion_limit=25`, ≤2 verification passes, per-query token/cost budget, wall-clock timeout.
State: typed `GraphState` with reducers. Checkpointing: `PostgresSaver`, `thread_id = conversation_id`.

## 8. Observability

structlog JSON logs (request_id, org_id, trace_id). OpenTelemetry for FastAPI, Celery, SQLAlchemy and httpx.
Langfuse for LLM traces, token usage and cost (optional key). Per-stage processing metrics go in
`processing_jobs.metrics`. Per-query usage goes in `query_runs`.

## 9. Deployment topology (AWS)

ALB → ECS Fargate (`api`, `worker-cpu`, `worker-io`) · RDS PostgreSQL 16 (pgvector) · ElastiCache Redis ·
S3 (SSE-KMS) · ECR · CloudWatch · Secrets Manager. Terraform lives in `infrastructure/terraform`, kept
separate from application code. Nothing is applied without explicit approval and a budget alarm.

## 10. Security

See `docs/adr/0008-security-model.md`. Summary: magic-byte MIME detection, size limits, zip-bomb guard,
`defusedxml`, sandboxed non-root workers, content-addressed storage keys, argon2id, rotating refresh
tokens with reuse detection, RLS, Redis token-bucket rate limits, signed URLs, append-only audit log,
and a prompt-injection test suite.
