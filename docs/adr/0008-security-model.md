# ADR 0008 — Security model

- **Status:** Accepted (2026-11-18)
- **Decision:**
  - Uploads: magic-byte MIME detection, allow-list, size limit (50 MB default), OOXML zip-bomb guard, `defusedxml`, encrypted-PDF rejection, optional ClamAV.
  - Parsing in non-root, read-only-rootfs worker containers with memory/time limits.
  - Content-addressed storage keys. User filenames are metadata only. Signed short-lived URLs.
  - argon2id. Short-lived access JWT. Rotating refresh tokens with family revocation on reuse.
  - Tenant isolation in the app **and** Postgres RLS. The app role is not the table owner.
  - Document text is treated as untrusted. Agent tools are read-only. A prompt-injection test suite is maintained.
  - Redis token-bucket rate limits and per-query cost budgets. Append-only audit log. Secrets only from env / Secrets Manager.
