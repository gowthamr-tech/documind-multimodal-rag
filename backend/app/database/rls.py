"""SQL builders for Row-Level Security, used by Alembic migrations.

Policies compare `org_id` with the transaction-local `app.org_id` setting (see session.py).
`NULLIF(..., '')` matters: after a transaction-local setting ends, Postgres leaves the GUC as ''
on that pooled connection, and `''::uuid` would raise instead of matching nothing.

FORCE ROW LEVEL SECURITY is deliberately not used: the app connects as a non-owner role with
NOBYPASSRLS (so policies always apply to it), while the owner role runs migrations/maintenance.
"""

APP_ROLE = "documind_app"
CURRENT_ORG = "NULLIF(current_setting('app.org_id', true), '')::uuid"


def enable_tenant_rls(table: str) -> list[str]:
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (org_id = {CURRENT_ORG}) WITH CHECK (org_id = {CURRENT_ORG})",
    ]


def disable_tenant_rls(table: str) -> list[str]:
    return [
        f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    ]


def grant_app_role() -> str:
    """Idempotent grants for the app role (no-op if the role does not exist, e.g. some CI)."""
    # Identifiers are module constants, never user input.
    return f"""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT USAGE ON SCHEMA public TO {APP_ROLE};
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE};
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};
        REVOKE UPDATE, DELETE ON audit_logs FROM {APP_ROLE};
        REVOKE ALL ON alembic_version FROM {APP_ROLE};
      END IF;
    END $$;
    """  # noqa: S608
