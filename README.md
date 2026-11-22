# DocuMind AI

Multimodal document intelligence: understand, extract, retrieve, compare and reason over enterprise
documents (PDF, scanned PDF, images, DOCX, XLSX, PPTX), with verifiable, page-level citations.

**Stack:** FastAPI · PostgreSQL + pgvector · Redis + Celery · Docling · LangGraph · Gemini

This repository holds the backend (API, workers, agents), evaluation and infrastructure. The
Next.js web client lives in its own repository, **`documind-frontend`** (see
[ADR 0011](docs/adr/0011-separate-frontend-repository.md)).

> **Status: active development.** Phases 1–5 (backend) are implemented and tested; Phase 6
> (frontend) is in progress; Phases 7–8 (evaluation, AWS deployment) are not started.
> [`docs/CHECKLIST.md`](docs/CHECKLIST.md) is the source of truth for what is implemented and
> verified. Nothing beyond that list should be assumed to work.

## What it does

- **Document understanding:** layout-aware parsing, OCR for scans, table extraction, chart/figure
  analysis with a vision model.
- **Advanced RAG:** hybrid retrieval (dense + lexical) with reranking over page-anchored chunks.
- **Agentic reasoning:** a LangGraph supervisor routes questions to table, chart, retrieval and
  reasoning specialists. Arithmetic runs in code, not in the LLM.
- **Verification before display:** every answer is checked for citation existence, numeric
  grounding and lexical support before it is streamed to the user (SSE). Unverified tokens are
  never shown.
- **Human-in-the-loop:** sensitive actions pause at an approval gate and resume across requests
  using Postgres checkpointing.

## Verified so far

| Area | Result |
|---|---|
| Backend | 170 tests passing; ruff + mypy `--strict` clean (`make check`) |
| Retrieval | benchmark results in [`evaluation/results/`](evaluation/results/) |

**Known gaps:** the vision-model and LLM agent paths have only been tested against a scripted model.
They have not yet run against a live API key.

## Documentation

- [Architecture](docs/architecture.md)
- [Database design](docs/database-design.md)
- [API contracts](docs/api-contracts.md)
- [Implementation roadmap](docs/implementation-roadmap.md)
- [Architecture Decision Records](docs/adr/)

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (installs Python 3.12 automatically), Node 22, and a
container runtime (OrbStack or Colima on macOS).

```bash
cp .env.example backend/.env
docker compose up -d   # Postgres + pgvector, Redis
make install           # uv sync
make check             # ruff + mypy (strict) + pytest with coverage
make run               # http://localhost:8000/api/v1/health/live  ·  docs at /api/v1/docs
```

For the web UI, start the backend as above and follow the `documind-frontend` README.

## Repository layout

```
backend/         FastAPI app, Celery workers, LangGraph agents, migrations, tests
evaluation/      benchmarks and results
infrastructure/  docker; AWS / Terraform (Phase 8)
docs/            architecture, contracts, ADRs, checklist
```
