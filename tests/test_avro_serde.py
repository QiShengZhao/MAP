import io
import struct

import fastavro
import pytest

from app.eventbus.schemas import RUN_EVENT_V1, RUN_EVENT_V2
from app.eventbus import avro_serde


@pytest.fixture
def fake_registry(monkeypatch):
    store = {}

    class FakeReg:
        async def register(self, subject, schema):
            sid = 1 if schema is RUN_EVENT_V1 else 2
            store[sid] = fastavro.parse_schema(schema)
            return sid

        async def get_schema(self, sid):
            return store[sid]

    monkeypatch.setattr(avro_serde, "registry", lambda: FakeReg())
    return store


def _v2_event(**over):
    base = dict(event_id="e1", run_id="r1", tenant_id="t1", seq=1,
                event_type="tool.call", ts_ms=1700000000000, payload="{}",
                workspace_id=None, agent_name="main", cost_usd=0.01, trace_id=None)
    base.update(over)
    return base


async def test_roundtrip_wire_format(fake_registry):
    data = await avro_serde.avro_encode("run-events-value", _v2_event())
    assert data[0:1] == b"\x00"
    assert struct.unpack(">I", data[1:5])[0] in fake_registry
    out = await avro_serde.avro_decode(data)
    assert out["agent_name"] == "main"


def test_backward_compat_v1_data_read_by_v2():
    v1 = {k: v for k, v in _v2_event().items()
          if k in {f["name"] for f in RUN_EVENT_V1["fields"]}}
    buf = io.BytesIO()
    fastavro.schemaless_writer(buf, fastavro.parse_schema(RUN_EVENT_V1), v1)
    buf.seek(0)
    out = fastavro.schemaless_reader(buf, fastavro.parse_schema(RUN_EVENT_V1),
                                     reader_schema=fastavro.parse_schema(RUN_EVENT_V2))
    assert out["workspace_id"] is None
