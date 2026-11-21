# ADR 0001 — Tooling and repository layout

- **Status:** Accepted (2026-11-18)
- **Context:** Greenfield monorepo with a Python backend, a TS frontend, infrastructure and evaluation. The dev machine has 23 GB free disk and `~/Documents` is iCloud-synced and already a git repo.
- **Decision:** Monorepo at `~/Documents/CV/documind-ai` (owner's choice; own nested git repo inside the iCloud-synced `~/Documents`). To keep heavy, regenerable directories out of iCloud, they use the `.nosync` suffix (iCloud skips it) behind a symlink, e.g. `backend/.venv -> .venv.nosync`; the same pattern will apply to `node_modules` and model caches. Backend managed with **uv** (`pyproject.toml` + `uv.lock`, dependency groups instead of `requirements/*.txt`), **Python 3.12**. Lint/format with **ruff**, types with **mypy (strict on `app/`)**, tests with **pytest**. Shared provider abstractions live in `app/providers/`. API schemas (`app/schemas/`) are separate from ORM models.
- **Consequences:** Reproducible locks and fast installs. One extra concept (`uv`) for contributors. Python 3.14 on the host is not used because parts of the ML stack lag new CPython releases.
