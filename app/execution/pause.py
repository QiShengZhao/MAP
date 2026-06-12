"""PauseController：聚合暂停信号，Runner 在安全点查询。"""
import asyncio
import json
import logging

from app.infra.redis_client import get_redis

log = logging.getLogger("execution.pause")


class PauseController:
    def __init__(self, tenant_id: str, run_id: str):
        self.tenant_id, self.run_id = tenant_id, run_id
        self._flag = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _listen(self) -> None:
        try:
            r = await get_redis()
            ps = r.pubsub()
            await ps.subscribe(f"tenant:{self.tenant_id}:control",
                               f"run:{self.run_id}:control")
            async for msg in ps.listen():
                if msg["type"] != "message":
                    continue
                try:
                    data = msg.get("data")
                    if isinstance(data, bytes):
                        data = data.decode()
                    op = json.loads(data)
                    if op.get("op") == "pause":
                        self._flag.set()
                except (json.JSONDecodeError, TypeError):
                    pass
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("pause listener error (poll fallback still active)")

    async def pause_requested(self) -> str | None:
        r = await get_redis()
        tenant_pause = await r.get(f"risk:paused:{self.tenant_id}")
        if tenant_pause:
            info = json.loads(tenant_pause)
            return f"risk:{info.get('rule', 'unknown')}"
        run_pause = await r.get(f"run:{self.run_id}:pause")
        if run_pause:
            raw = run_pause.decode() if isinstance(run_pause, bytes) else run_pause
            return f"manual:{raw}"
        if self._flag.is_set():
            self._flag.clear()
        return None
