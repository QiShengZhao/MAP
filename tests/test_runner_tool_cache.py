"""Runner resume 时工具调用幂等缓存。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.domain.models import Run, RunStatus
from app.execution.runner import Runner


async def test_resume_uses_cached_tool_result_without_reexecute(db_session):
    run = Run(id="run-tool-1", tenant_id="t1", session_id="s1",
              user_id="u1", status=RunStatus.running)
    db_session.add(run)
    await db_session.commit()

    runner = Runner("t1", "run-tool-1")
    runner._tool_results = {"call-1": '{"ok": true}'}
    call = SimpleNamespace(id="call-1", name="echo", args={"x": 1})
    tools = SimpleNamespace(
        requires_approval=lambda name, policy: False,
        execute=AsyncMock(return_value="should-not-run"),
    )
    policy = {}
    usage = SimpleNamespace(add_tool_call=lambda *a, **k: None)

    emitted = []
    async def capture_emit(db, type_, payload):
        emitted.append((type_, payload))

    runner.emit = capture_emit
    out = await runner._execute_tool(db_session, run, tools, policy, usage, call)
    assert out == '{"ok": true}'
    tools.execute.assert_not_called()
    assert any(t == "tool.result" and p.get("cached") for t, p in emitted)


async def test_history_tool_message_skips_reexecution():
    history = [
        {"role": "assistant", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "done"},
    ]
    assert Runner._history_has_tool_result(history, "c1")
    assert not Runner._history_has_tool_result(history, "c2")
