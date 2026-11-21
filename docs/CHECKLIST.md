# Implementation Checklist

Legend: ✅ done and verified · 🟡 partial · ⬜ not started · ⛔ blocked (reason given)

## Phase 1 — Architecture & planning ✅
- ✅ Environment inspected (findings in ADR 0001 and ADR 0010)
- ✅ `docs/architecture.md`, `database-design.md`, `api-contracts.md`, `implementation-roadmap.md`
- ✅ ADRs 0001–0010

## Phase 2 — Core infrastructure ✅ (frontend scaffold moved to Phase 6)
- ✅ 2.1 Repo, tooling (uv, ruff, mypy strict, pytest), settings, structured logging, request IDs, RFC 9457 errors
- ✅ 2.2 Docker Compose: Postgres 16 + pgvector 0.8.7, Redis 7; `GET /health/ready` (DB, Redis, storage)
- ✅ 2.4 Schema v1 (migrations 0001–0002) + Row-Level Security + non-owner app role; `alembic check` drift test
- ✅ 2.5 Auth: argon2id, JWT access (alg pinned), rotating refresh tokens with family revocation, roles, append-only audit log
- ✅ 2.6 Storage (local + S3) + upload validation (magic bytes, size cap while streaming, encrypted PDF, zip bomb, path traversal, image bomb, extension mismatch) + SHA-256 dedupe + versioning + document endpoints
- ✅ 2.7 Celery (acks_late, recycling, routing) + pipeline runner (atomic claim, idempotent, per-stage jobs) + status + processing history + purge task
- ✅ 2.8 Redis token-bucket rate limiting (global / auth per-IP / upload), fail-open on Redis outage
- ✅ CI workflow with Postgres + Redis service containers (**not yet run on GitHub**: no remote)
- ⬜ 2.3 Frontend scaffold → done in Phase 6
- Known gaps: `Idempotency-Key` header not implemented (uploads are idempotent by content hash instead); API/worker Dockerfiles land in Phase 8.

## Phase 3 — Document understanding ✅ (vision-model calls ⛔ untested live: no `GEMINI_API_KEY`)
- ✅ Format routing; page rendering (pypdfium2); per-page text-layer probe → Tesseract OCR only where needed
- ✅ Docling layout → normalised Document Graph (pages, elements, bboxes, reading order, section paths)
- ✅ Tables: spans, multi-level header paths, units/scale, accounting numbers, footnotes, cross-page stitching
- ✅ Figures: classifier (chart/diagram/image), crops, captions; chart titles inside figures not treated as headings
- ✅ Charts via VLM (Gemini adapter) + **OCR cross-validation**; values flagged as estimates unless OCR-verified
- ✅ Office: DOCX/XLSX/PPTX native (LibreOffice → PDF path when installed; not installed on this host)
- ✅ Migration 0003 + RLS; endpoints: tables, charts, pages, page images, elements, file
- Measured: synthetic report tables cell-F1 ≥ 0.95 (test threshold); OCR validator catches injected wrong chart values

## Phase 4 — Advanced RAG ✅
- ✅ 7 chunking strategies (fixed, recursive, semantic, structure, page, table_aware, parent_child) with full provenance
- ✅ Embedding providers: local MiniLM (default, no key), Gemini + OpenAI adapters (**mock-tested only**)
- ✅ pgvector HNSW per embedding space (iterative scans), Postgres lexical, RRF, cross-encoder rerank, metadata filters
- ✅ Context assembly (parent expansion, token budget, per-document balancing) + server-side citation resolution
- ✅ `POST /api/v1/search`; retrieval experiment runner + first results in `evaluation/results/`
- ⚠️ Known limitation: synthetic docs too short to separate chunking strategies (fixed = 1 chunk/doc). Fix in Phase 7.
- Deviation: default embedding model is all-MiniLM-L6-v2 (was cached locally; BGE download impossible on the build network)

## Phase 5 — Agentic intelligence ✅ (live LLM ⛔ untested: scripted model in tests)
- ✅ LangGraph graph: supervisor (LLM or rule fallback), approval gate (HITL `interrupt`), document classifier,
  retrieval, table + chart analysts (deterministic facts from cells), reasoning, verification, response
- ✅ Conditional routing (specialists run only when evidence needs them), RetryPolicy, recursion limit, bounded verify loop
- ✅ Calculator: arithmetic in code; `{{calc}}` placeholders; operands must come from facts or appear in sources
- ✅ Verification: citation existence, numeric grounding, lexical support, optional LLM judge (downgrade-only)
- ✅ Postgres checkpointing; approvals resume across requests; **security bug found & fixed** (gate used approver role on resume)
- ✅ `/query`, `/query/compare`, SSE streaming (progress + verified answer), conversations, runs, approvals
- Streaming design: answers are released only after verification (no unverified tokens are shown).
## Phase 6 — Frontend ⬜
## Phase 7 — Evaluation ⬜
## Phase 8 — Deployment ⬜

## Verified results log

| Date | Milestone | Command | Result |
|---|---|---|---|
| 2026-11-18 | 2.1 | `make check` | 18 passed, ruff + mypy strict clean |
| 2026-11-18 | 2.2–2.8 | `make check` (Postgres + Redis running) | **100 passed**, ruff + mypy strict clean (41 files) |
| 2026-11-18 | 2.7 | real `celery worker` + `uvicorn` + curl upload of a 5-page PDF | queued → ready in ~0.1 s, `fetch_verify` sha256 verified |
| 2026-11-21 | 3–5 | `make check` | **170 passed**, ruff + mypy strict clean (91 files) |
| 2026-11-21 | 4.7 | `python -m benchmarks.retrieval_experiment --docs 6` | results in `evaluation/results/retrieval_*.md` (see caveat) |
