# ADR 0010 — Local development environment

- **Status:** Accepted (2026-11-18)
- **Decision (revised 2026-11-18):** **Colima** (Lima VM, `vz` backend) as the container runtime. OrbStack was the first choice, but this Mac's Homebrew is owned by a different macOS user and OrbStack needs a GUI approval step. Colima, Lima, the Docker CLI and Compose were installed as checksum-verified release binaries into `~/.local` (no admin rights). Start with `colima start --vm-type vz --cpu 4 --memory 6 --disk 40`. Docker Compose runs `pgvector/pgvector:pg16` (host port **5433**, since a host Postgres already uses 5432) and `redis:7-alpine` (host port **6380**); `api`/`worker`/`web` containers live under the `app` profile. Host Homebrew Postgres/Redis are left untouched (no pgvector there). Local file storage in dev, S3 in prod (tested with `moto`).
- **Location:** The repo lives in iCloud-synced `~/Documents/CV`; see ADR 0001 for the `.nosync` convention. It is a nested git repo inside the `~/Documents` repo — never `git add` it from the parent.
- **Consequences:** Docker is required from milestone 2.2. Developers should keep ≥ 15 GB free.
