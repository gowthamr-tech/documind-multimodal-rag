.PHONY: install lint format typecheck test check run

BACKEND := cd backend &&

install:   ## Install backend dependencies (uv, Python 3.12)
	$(BACKEND) uv sync

lint:
	$(BACKEND) uv run ruff check . && uv run ruff format --check .

format:
	$(BACKEND) uv run ruff check --fix . && uv run ruff format .

typecheck:
	$(BACKEND) uv run mypy

test:
	$(BACKEND) uv run pytest --cov=app --cov-report=term-missing

check: lint typecheck test   ## Everything CI runs

run:       ## Dev API server on :8000
	$(BACKEND) uv run uvicorn app.main:app --reload --port 8000
