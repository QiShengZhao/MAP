"""签发/验签/刷新/吊销。kid 轮换流程：
1. JWT_KEYS 加入新 kid（旧 kid 保留） 2. JWT_ACTIVE_KID 切到新 kid（滚动发布）
3. 等旧 access token 全部自然过期（15min）后，下个发布周期移除旧 kid
"""
import time
import uuid

import jwt

from app.config import settings


def _keys() -> dict[str, str]:
    return settings.JWT_KEYS or {settings.JWT_ACTIVE_KID: settings.JWT_SECRET}


def _issue(sub: str, tenant_id: str, role: str, typ: str, ttl: int) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "tid": tenant_id, "role": role, "typ": typ,
         "iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex},
        _keys()[settings.JWT_ACTIVE_KID], algorithm="HS256",
        headers={"kid": settings.JWT_ACTIVE_KID})


def issue_access(sub: str, tenant_id: str, role: str) -> str:
    return _issue(sub, tenant_id, role, "access", settings.JWT_ACCESS_TTL_SECONDS)


def issue_refresh(sub: str, tenant_id: str) -> str:
    return _issue(sub, tenant_id, "", "refresh", settings.JWT_REFRESH_TTL_SECONDS)


def verify(token: str) -> dict:
    header = jwt.get_unverified_header(token)
    if header.get("alg") != "HS256":
        raise jwt.InvalidTokenError("unexpected alg")
    key = _keys().get(header.get("kid", settings.JWT_ACTIVE_KID))
    if not key:
        raise jwt.InvalidTokenError(f"unknown kid: {header.get('kid')}")
    return jwt.decode(token, key, algorithms=["HS256"],
                      options={"require": ["exp", "sub", "tid", "jti", "typ"]})


async def revoke(jti: str, exp: int) -> None:
    from app.infra.redis_client import get_redis
    ttl = max(exp - int(time.time()), 1)
    r = await get_redis()
    await r.set(f"jwt:revoked:{jti}", "1", ex=ttl)
