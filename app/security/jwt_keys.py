"""JWT 签发/验签：kid 轮换。"""
import time
import uuid

import jwt

from app.config import settings


def _keys() -> dict[str, str]:
    if settings.JWT_KEYS:
        return settings.JWT_KEYS
    return {settings.JWT_ACTIVE_KID: settings.JWT_SECRET}


def issue_access(sub: str, tenant_id: str = "", role: str = "") -> str:
    now = int(time.time())
    kid = settings.JWT_ACTIVE_KID
    payload = {"sub": sub, "exp": now + settings.JWT_ACCESS_TTL_SECONDS,
               "iat": now, "jti": uuid.uuid4().hex, "typ": "access"}
    if tenant_id:
        payload["tid"] = tenant_id
    if role:
        payload["role"] = role
    return jwt.encode(payload, _keys()[kid], algorithm="HS256", headers={"kid": kid})


def verify(token: str) -> dict:
    kid = jwt.get_unverified_header(token).get("kid", settings.JWT_ACTIVE_KID)
    key = _keys().get(kid)
    if not key:
        raise jwt.InvalidTokenError(f"unknown kid: {kid}")
    opts = {"require": ["exp", "sub"]}
    return jwt.decode(token, key, algorithms=["HS256"], options=opts)
