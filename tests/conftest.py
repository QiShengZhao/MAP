import os

os.environ.update(
    ENV="test", EVENT_BUS="redis", SANDBOX_BACKEND="local",
    JWT_SECRET="test-secret-key-at-least-32-bytes-long!!",
    JWT_ACTIVE_KID="test",
    DATABASE_URL="sqlite+aiosqlite:///:memory:",
)

import pytest
from contextlib import asynccontextmanager

try:
    import fakeredis.aioredis
except ImportError:
    fakeredis = None

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.domain.models import Base


@pytest.fixture
async def redis():
    if fakeredis is None:
        pytest.skip("fakeredis not installed")
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        yield s


async def _ret(v):
    return v


@asynccontextmanager
async def _session_ctx(factory):
    async with factory() as s:
        yield s


@pytest.fixture
async def client(redis, db_engine, monkeypatch):
    from app import main
    from app.infra import redis_client, db

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    monkeypatch.setattr(redis_client, "get_redis", lambda: _ret(redis))
    monkeypatch.setattr(redis_client, "redis_client", redis)

    @asynccontextmanager
    async def _factory():
        async with factory() as s:
            yield s

    monkeypatch.setattr(db, "session_factory", _factory)
    monkeypatch.setattr(db, "SessionLocal", factory)

    async def _get_db():
        async with factory() as s:
            yield s

    monkeypatch.setattr(db, "get_db", _get_db)

    from app.runtime import budget as budget_mod
    monkeypatch.setattr(budget_mod, "redis_client", redis)

    async with AsyncClient(transport=ASGITransport(app=main.app),
                           base_url="http://test") as c:
        yield c
