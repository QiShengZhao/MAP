"""Confluent wire format: [0x00][4B schema_id BE][avro binary]，带本地缓存。"""
import io
import struct
import asyncio

import fastavro
import httpx

from app.config import settings
from app.eventbus.schemas import SUBJECTS

_MAGIC = b"\x00"


class SchemaRegistryClient:
    def __init__(self, url: str | None = None):
        self.url = (url or settings.SCHEMA_REGISTRY_URL).rstrip("/")
        self._id_by_subject: dict[str, int] = {}
        self._schema_by_id: dict[int, dict] = {}
        self._lock = asyncio.Lock()
        auth = None
        if settings.SCHEMA_REGISTRY_USER:
            auth = (settings.SCHEMA_REGISTRY_USER, settings.SCHEMA_REGISTRY_PASSWORD)
        self._http = httpx.AsyncClient(base_url=self.url, auth=auth, timeout=10)

    async def register(self, subject: str, schema: dict) -> int:
        if subject in self._id_by_subject:
            return self._id_by_subject[subject]
        async with self._lock:
            if subject in self._id_by_subject:
                return self._id_by_subject[subject]
            import json
            r = await self._http.post(
                f"/subjects/{subject}/versions",
                json={"schema": json.dumps(schema), "schemaType": "AVRO"},
            )
            r.raise_for_status()
            sid = r.json()["id"]
            self._id_by_subject[subject] = sid
            self._schema_by_id[sid] = fastavro.parse_schema(schema)
            return sid

    async def get_schema(self, schema_id: int) -> dict:
        if schema_id in self._schema_by_id:
            return self._schema_by_id[schema_id]
        import json
        r = await self._http.get(f"/schemas/ids/{schema_id}")
        r.raise_for_status()
        parsed = fastavro.parse_schema(json.loads(r.json()["schema"]))
        self._schema_by_id[schema_id] = parsed
        return parsed

    async def set_compatibility(self, subject: str, level: str = "BACKWARD"):
        await self._http.put(f"/config/{subject}", json={"compatibility": level})

    async def close(self):
        await self._http.aclose()


_registry: SchemaRegistryClient | None = None


def registry() -> SchemaRegistryClient:
    global _registry
    if _registry is None:
        _registry = SchemaRegistryClient()
    return _registry


async def avro_encode(subject: str, record: dict) -> bytes:
    schema = SUBJECTS[subject]
    sid = await registry().register(subject, schema)
    buf = io.BytesIO()
    fastavro.schemaless_writer(buf, fastavro.parse_schema(schema), record)
    return _MAGIC + struct.pack(">I", sid) + buf.getvalue()


async def avro_decode(data: bytes) -> dict:
    if not data or data[0:1] != _MAGIC:
        raise ValueError("not confluent wire format")
    sid = struct.unpack(">I", data[1:5])[0]
    schema = await registry().get_schema(sid)
    return fastavro.schemaless_reader(io.BytesIO(data[5:]), schema)
