"""轻量 webhook 通知（风控 / 调度器等复用）。"""
import hashlib
import hmac
import json
import logging
import time

import httpx

from app.config import settings

log = logging.getLogger("notifications")


async def send_webhook(payload: dict, *, url: str | None = None) -> bool:
    target = url or settings.RISK_DEFAULT_WEBHOOK
    if not target:
        return False
    body = json.dumps(payload, ensure_ascii=False)
    secret = settings.RISK_WEBHOOK_SECRET or ""
    headers = {"Content-Type": "application/json"}
    if secret:
        ts = str(int(time.time()))
        sig = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
        headers["X-Risk-Timestamp"] = ts
        headers["X-Risk-Signature"] = sig
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(target, content=body, headers=headers)
            if resp.status_code >= 400:
                log.warning("webhook %s returned %s", target, resp.status_code)
                return False
        return True
    except Exception:
        log.exception("webhook failed url=%s", target)
        return False
