#!/usr/bin/env bash
# Runs once, on first initialisation of the data volume.
# - The owner role (POSTGRES_USER) owns all tables and runs migrations.
# - documind_app is what the API and workers connect as. It is NOT the table owner and has
#   NOBYPASSRLS, so Row-Level Security policies always apply to it.
# - documind_test is a separate database used by the integration test suite.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE documind_app LOGIN PASSWORD '${DOCUMIND_APP_PASSWORD}' NOSUPERUSER NOBYPASSRLS;
CREATE DATABASE documind_test OWNER ${POSTGRES_USER};
SQL

for db in "$POSTGRES_DB" documind_test; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<SQL
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;
GRANT CONNECT ON DATABASE ${db} TO documind_app;
GRANT USAGE ON SCHEMA public TO documind_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO documind_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO documind_app;
SQL
done
