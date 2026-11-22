# ADR 0011 — Separate frontend repository

- **Status:** Accepted (2026-11-21). Supersedes the monorepo part of ADR 0001.
- **Context:** The Next.js frontend lived in `frontend/` next to the backend. It had none of the
  backend's guarantees (no lint, no CI job, no runtime validation of API responses), and the two halves
  have different toolchains, release cadences and deployment targets. They already talk only over HTTP,
  through `docs/api-contracts.md`.
- **Decision:** Move the frontend to its own repository, **`documind-frontend`** (sibling directory
  `~/Documents/CV/documind-frontend`), with its git history preserved (`git subtree split`). This
  repository keeps the backend, evaluation, infrastructure and the API contract. The frontend repo has
  its own CI (eslint with 0 warnings, `tsc` strict, vitest with coverage thresholds, production build),
  and it validates every backend response at runtime with zod schemas that mirror `app/schemas/`.
- **Consequences:**
  - **Contract changes now cross a repo boundary.** A breaking change to a response model needs a
    matching change to `documind-frontend/src/lib/contracts.ts`. Additive changes are safe because the
    frontend ignores unknown fields. A follow-up is to generate the frontend types from the backend's
    OpenAPI schema so drift is caught in CI rather than at runtime.
  - The `web` service mentioned in ADR 0010 will come from the frontend repo's own image (Phase 8).
  - Backend CORS settings are unaffected: the browser only talks to the frontend's BFF, which calls
    the API server-to-server.
