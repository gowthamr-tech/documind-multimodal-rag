# DocuMind AI — Backend

FastAPI service plus (from milestone 2.7) Celery workers. See `../docs/architecture.md`.

```bash
uv sync                      # install (Python 3.12, managed by uv)
uv run uvicorn app.main:app --reload
uv run pytest                # tests
uv run ruff check . && uv run ruff format --check . && uv run mypy
```
