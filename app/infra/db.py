from contextlib import asynccontextmanager
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings

_engine_kw: dict = {}
if not settings.DATABASE_URL.startswith("sqlite"):
    _engine_kw = dict(pool_size=20, max_overflow=20, pool_pre_ping=True)

engine = create_async_engine(settings.DATABASE_URL, **_engine_kw)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def session_factory():
    """审计写入等后台任务使用的 service 级会话。"""
    async with SessionLocal() as session:
        if session.bind and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT set_config('app.is_service', 'true', true)"))
        yield session


async def get_db():
    async with SessionLocal() as session:
        yield session

@asynccontextmanager
async def tenant_session(tenant_id: str):
    """带 RLS 上下文的会话：DB 层兜底租户隔离"""
    async with SessionLocal() as session:
        await set_tenant_context(session, tenant_id)
        yield session


async def set_tenant_context(session: AsyncSession, tenant_id: str) -> None:
    if session.bind and session.bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": tenant_id})

RLS_SQL = """
DO $$
DECLARE t RECORD;
BEGIN
  FOR t IN
    SELECT table_name FROM information_schema.columns
    WHERE column_name = 'tenant_id' AND table_schema = 'public'
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t.table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t.table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t.table_name);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING '
      '(tenant_id = current_setting(''app.tenant_id'', true) '
      ' OR current_setting(''app.is_service'', true) = ''true'') '
      'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true) '
      ' OR current_setting(''app.is_service'', true) = ''true'')', t.table_name);
  END LOOP;
END $$;
"""
