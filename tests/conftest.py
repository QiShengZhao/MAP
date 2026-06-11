import os

os.environ.update(
    ENV="test",
    EVENT_BUS="redis",
    SANDBOX_BACKEND="local",
    JWT_SECRET="test-secret-key-at-least-32-bytes-long!!",
    DATABASE_URL="sqlite+aiosqlite:///:memory:",
)

import pytest

try:
    import fakeredis.aioredis
except ImportError:
    fakeredis = None


@pytest.fixture
async def redis():
    if fakeredis is None:
        pytest.skip("fakeredis not installed")
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()
