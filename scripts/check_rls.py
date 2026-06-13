"""Fail fast when PostgreSQL tenant RLS can be bypassed."""
import os
import uuid

import psycopg2


def main() -> None:
    url = os.environ.get(
        "RLS_DATABASE_URL",
        "postgresql://agent_runtime:agent_runtime@localhost:5432/agent_platform",
    )
    tenant_a = f"rls-a-{uuid.uuid4().hex[:8]}"
    tenant_b = f"rls-b-{uuid.uuid4().hex[:8]}"
    workspace_a = str(uuid.uuid4())
    workspace_b = str(uuid.uuid4())

    with psycopg2.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.is_service', 'true', false)")
            cur.execute(
                "INSERT INTO tenants (id, name, slug, plan, settings, created_at) "
                "VALUES (%s, 'RLS A', %s, 'free', '{}', now()), "
                "(%s, 'RLS B', %s, 'free', '{}', now())",
                (tenant_a, tenant_a, tenant_b, tenant_b),
            )
            cur.execute(
                "INSERT INTO workspaces (id, tenant_id, name, created_at) "
                "VALUES (%s, %s, 'A', now()), (%s, %s, 'B', now())",
                (workspace_a, tenant_a, workspace_b, tenant_b),
            )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("RESET app.is_service")
            cur.execute("RESET app.tenant_id")
            cur.execute("SELECT count(*) FROM workspaces")
            assert cur.fetchone()[0] == 0, "missing tenant context exposed rows"

            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_a,))
            cur.execute("SELECT tenant_id FROM workspaces")
            assert cur.fetchall() == [(tenant_a,)], "tenant read isolation failed"

            try:
                cur.execute(
                    "INSERT INTO workspaces "
                    "(id, tenant_id, name, created_at) VALUES (%s, %s, 'bad', now())",
                    (str(uuid.uuid4()), tenant_b),
                )
            except psycopg2.errors.InsufficientPrivilege:
                conn.rollback()
            else:
                raise AssertionError("tenant write isolation failed")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_class "
                "WHERE relrowsecurity AND NOT relforcerowsecurity "
                "AND relnamespace = 'public'::regnamespace")
            assert cur.fetchone()[0] == 0, "RLS is enabled but not forced"

    print("PostgreSQL RLS isolation verified")


if __name__ == "__main__":
    main()
