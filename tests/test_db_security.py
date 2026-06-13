from app.infra.db import RLS_SQL


def test_rls_is_forced_and_denies_missing_tenant_context():
    assert "FORCE ROW LEVEL SECURITY" in RLS_SQL
    assert "WITH CHECK" in RLS_SQL
    assert "current_setting(''app.tenant_id'', true) IS NULL" not in RLS_SQL


def test_rls_has_explicit_service_context_for_background_jobs():
    assert "app.is_service" in RLS_SQL
