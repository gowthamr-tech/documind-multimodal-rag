# Implementation Roadmap

Each phase ends with passing tests, real output, updated docs and checklist (`docs/CHECKLIST.md`),
and a demo command. Strategy: **vertical slice first** (native PDF → tables → hybrid retrieval → cited
answer), then broaden formats.

| Phase | Milestones | Exit criteria (evidence) |
|---|---|---|
| 2 Core infrastructure | 2.1 repo, tooling, CI lint/test · 2.2 Compose (pgvector, Redis, api, worker) + readiness · 2.3 config, logging, error envelope (done in 2.1) · 2.4 schema v1 + Alembic + RLS · 2.5 auth + roles + audit · 2.6 storage + upload validation + dedupe · 2.7 Celery skeleton pipeline + status · 2.8 rate limiting | `docker compose up` healthy; upload → job → `ready`; cross-tenant + security tests pass |
| 3 Document understanding | 3.1 classify + render · 3.2 Docling parse → Document Graph · 3.3 OCR path · 3.4 tables · 3.5 charts (VLM) · 3.6 images · 3.7 Office formats via LibreOffice | per-module tests on fixtures; table cell P/R on synthetic tables |
| 4 Advanced RAG | 4.1 chunkers · 4.2 embedding providers + spaces · 4.3 dense/lexical/RRF · 4.4 reranker · 4.5 expansion · 4.6 context assembly + citation resolution · 4.7 retrieval experiments | Recall@K / MRR / NDCG tables from real runs |
| 5 Agents | 5.1 state + graph + routing tests · 5.2 retrieval/table/chart nodes · 5.3 calculator + reasoning · 5.4 verification loop · 5.5 compare · 5.6 checkpointing, HITL, limits · 5.7 SSE | route unit tests; live E2E once an API key exists |
| 6 Frontend | 6.1 shell + auth + design system · 6.2 library/upload/status · 6.3 viewer + bbox highlights · 6.4 streaming chat + citations → viewer · 6.5 tables & charts · 6.6 compare · 6.7 history + eval + dashboard | Playwright smoke tests; citation click → correct page + highlight |
| 7 Evaluation | 7.1 dataset + human verification · 7.2 extraction (CER/WER, TEDS, cell P/R, chart accuracy) · 7.3 retrieval grid · 7.4 generation metrics · 7.5 hallucination & citation tests · 7.6 latency/cost/failure rate | `evaluation/results/*.md` with config, dataset hash, git SHA |
| 8 Deployment | 8.1 prod images · 8.2 Terraform · 8.3 CloudWatch · 8.4 CI/CD → ECR/ECS · 8.5 security validation · 8.6 runbooks | `terraform plan` clean; apply only with approval |

## Testing strategy

- **Unit:** pure logic, routing functions, metrics, chunkers, unit normalisation.
- **Integration:** real Postgres + Redis via Compose (never SQLite, because RLS and pgvector differ).
- **API:** httpx `AsyncClient` against the app.
- **Security:** upload attack fixtures, cross-tenant access, JWT tampering, prompt-injection documents.
- **Model-dependent:** `@pytest.mark.live`. Skipped with an explicit reason when keys are missing,
  and reported as not run.

## Evaluation data policy

Ground truth comes from (a) programmatically generated documents with known contents, (b) public-domain
SEC filings, and (c) small licensed subsets (e.g. FinTabNet, CDLA-Permissive). QA items are drafted with
evidence and become `verified` only when a human records `verified_by`. No benchmark number is published
without its config, dataset hash and git SHA.
