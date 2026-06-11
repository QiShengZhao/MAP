"""安全中间件：headers / body 限长 / Redis 滑窗限流。"""
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.infra.redis_client import get_redis

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy":
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        resp = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        if settings.ENV == "production":
            resp.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains; preload")
        return resp


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        cl = request.headers.get("content-length")
        if cl and int(cl) > settings.MAX_BODY_BYTES:
            return JSONResponse({"detail": "payload too large"}, status_code=413)
        return await call_next(request)


_RATE_LUA = """
local key, now, window, limit = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local n = redis.call('ZCARD', key)
if n >= limit then return 0 end
redis.call('ZADD', key, now, now .. '-' .. math.random(1000000))
redis.call('EXPIRE', key, math.ceil(window / 1000) + 1)
return 1
"""


def _parse(rule: str) -> tuple[int, int]:
    n, unit = rule.split("/")
    return int(n), {"second": 1000, "minute": 60000, "hour": 3600000}[unit]


async def rate_limit(request: Request, rule: str | None = None, key_suffix: str = ""):
    limit, window_ms = _parse(rule or settings.RATE_LIMIT_DEFAULT)
    ident = getattr(request.state, "tenant_id", None) \
        or (request.client.host if request.client else "unknown")
    r = await get_redis()
    ok = await r.eval(_RATE_LUA, 1, f"rl:{ident}:{request.url.path}:{key_suffix}",
                      int(time.time() * 1000), window_ms, limit)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=429, detail="rate limit exceeded",
                            headers={"Retry-After": str(window_ms // 1000)})
