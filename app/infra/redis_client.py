import redis.asyncio as redis
from app.config import settings

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True,
                              max_connections=50)


async def get_redis():
    """兼容 eventbus 模块的异步获取接口。"""
    return redis_client