import json
import logging
from datetime import datetime

from sqlalchemy import func, select

from app.config import settings
from app.domain.models import (ApprovalRequest, ApprovalStatus, Message, Run,
                               RunEvent, RunStatus, Skill)
from app.execution.pause import PauseController
from app.execution.run_statemachine import (StaleTransition, TERMINAL,
                                            transition)
from app.infra.db import tenant_session
from app.infra.redis_client import redis_client
from app.observability.tracing import tracer
from app.platform_services.cost_timeseries import CostTimeseries
from app.platform_services.policy import PolicyService
from app.platform_services.usage import UsageMeter
from app.runtime.agents import AgentRegistry
from app.runtime.approval import ApprovalService
from app.runtime.budget import BudgetExceeded, BudgetGuard
from app.runtime.guardrails import GuardrailBlocked, Guardrails
from app.runtime.model_router import ModelRouter
from app.runtime.sandbox import SandboxManager
from app.runtime.state import StateService, load_checkpoint, save_checkpoint, Checkpoint
from app.runtime.tools import ToolContext, ToolRegistry
from app.memory.service import MemoryService

log = logging.getLogger("runner")

DEFAULT_SYSTEM_PROMPT = """You are an AI agent on a multi-tenant platform.
Rules:
1. You only make DECISIONS; all actions must go through tools.
2. Never fabricate tool results. If a tool fails, report honestly.
3. Commands run inside an isolated sandbox; produced files become artifacts.
"""


class RunCancelled(Exception):
    pass


class RunOutcome:
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class Runner:
    def __init__(self, tenant_id, run_id, workspace_id=""):
        self.tenant_id, self.run_id, self.workspace_id = tenant_id, run_id, workspace_id
        self.seq = 0
        self.pause_ctl: PauseController | None = None
        self._pending_approval = None
        self._loop_ctx: dict = {}

    async def emit(self, db, type_, payload):
        from app.eventbus.bus import build_run_event, publish_run_event

        self.seq += 1
        if settings.EVENT_BUS == "kafka":
            event = build_run_event(
                run_id=self.run_id, tenant_id=self.tenant_id, seq=self.seq,
                event_type=type_, payload=payload,
                workspace_id=self.workspace_id or None)
            await publish_run_event(event)
            return

        db.add(RunEvent(tenant_id=self.tenant_id, run_id=self.run_id,
                        seq=self.seq, type=type_, payload=payload))
        await db.flush()
        legacy = json.dumps({"seq": self.seq, "type": type_, "payload": payload},
                            ensure_ascii=False)
        await redis_client.publish(
            f"tenant:{self.tenant_id}:run:{self.run_id}:events", legacy)

    async def _load_event_seq(self, db) -> int:
        return await db.scalar(select(func.coalesce(func.max(RunEvent.seq), 0))
                               .where(RunEvent.run_id == self.run_id)) or 0

    async def _check_cancel(self):
        if await redis_client.get(f"cancel:run:{self.run_id}"):
            raise RunCancelled()

    async def execute(self, *, resume: bool = False) -> str:
        outcome = RunOutcome.FAILED
        self.pause_ctl = PauseController(self.tenant_id, self.run_id)
        await self.pause_ctl.start()
        try:
            with tracer.start_as_current_span("agent.run") as span:
                span.set_attribute("tenant.id", self.tenant_id)
                span.set_attribute("run.id", self.run_id)
                async with tenant_session(self.tenant_id) as db:
                    run = await db.get(Run, self.run_id)
                    if not run:
                        return RunOutcome.FAILED
                    self.seq = await self._load_event_seq(db)
                    if run.status not in (RunStatus.queued, RunStatus.running,
                                          RunStatus.paused):
                        return run.status.value

                    if run.status == RunStatus.queued:
                        try:
                            await transition(db, self.run_id, "queued", "running",
                                             reason="resume" if resume else "start",
                                             commit=False)
                        except StaleTransition:
                            await db.refresh(run)
                            if run.status.value in TERMINAL:
                                return run.status.value
                            raise
                        run.trace_id = format(span.get_span_context().trace_id, "032x")
                        await self.emit(db, "run.started" if not resume else "run.resumed",
                                        {"run_id": self.run_id})
                        await db.commit()

                    policy = await PolicyService.get(db, self.tenant_id)
                    usage = UsageMeter(self.tenant_id, self.workspace_id, self.run_id)
                    tools = ToolRegistry.for_tenant(policy)
                    agents = await AgentRegistry.load(db, self.tenant_id)

                    if resume:
                        restored = await self._restore_from_checkpoint(db, run, agents, usage)
                        if restored:
                            current, turn, history = restored
                        else:
                            log.warning("resume without checkpoint run=%s, cold start", self.run_id)
                            current = agents.get(
                                run.agent_config.get("agent", agents.default_name))
                            turn, history = 0, await self._build_history(db, run, current)
                    else:
                        state = await StateService.load(db, self.tenant_id, self.run_id)
                        if state and state.history:
                            current = agents.get(state.current_agent)
                            turn, history = state.turn, state.history
                            await self.emit(db, "run.resumed",
                                            {"agent": current.name, "turn": turn})
                        else:
                            current = agents.get(
                                run.agent_config.get("agent", agents.default_name))
                            turn = 0
                            history = await self._build_history(db, run, current)

                    try:
                        loop_outcome = await self._agent_loop(
                            db, run, agents, current, tools, policy, usage, history, turn)
                        if loop_outcome == RunOutcome.PAUSED:
                            outcome = RunOutcome.PAUSED
                        else:
                            await StateService.clear(db, self.tenant_id, self.run_id)
                            outcome = loop_outcome or RunOutcome.COMPLETED
                    except RunCancelled:
                        try:
                            await transition(db, self.run_id, run.status.value,
                                             "cancelled", reason="user cancel", commit=False)
                        except StaleTransition:
                            pass
                        await self.emit(db, "run.cancelled", {})
                        await db.commit()
                        outcome = RunOutcome.CANCELLED
                    except GuardrailBlocked as e:
                        await self._fail(db, run, f"guardrail blocked: {e}", usage)
                        outcome = RunOutcome.FAILED
                    except Exception as e:
                        log.exception("run %s failed", self.run_id)
                        await self._fail(db, run, str(e), usage)
                        outcome = RunOutcome.FAILED
                    finally:
                        if outcome != RunOutcome.PAUSED:
                            await SandboxManager.release_for_run(
                                self.tenant_id, run.session_id, usage)
                            await usage.flush(db)
                            run.usage = usage.snapshot()
                            if outcome != RunOutcome.CANCELLED:
                                refreshed = await db.get(Run, self.run_id)
                                if refreshed and refreshed.status.value not in TERMINAL:
                                    run.finished_at = datetime.utcnow()
                            await db.commit()
                        else:
                            await SandboxManager.release_for_run(
                                self.tenant_id, run.session_id, usage)
                            await usage.flush(db)
                            run.usage = usage.snapshot()
                            await db.commit()
        finally:
            await self.pause_ctl.stop()
        return outcome

    async def _agent_loop(self, db, run, agents, current, tools,
                          policy, usage, history, start_turn=0) -> str:
        router = ModelRouter.get()
        paused_from = "running"
        self._loop_ctx = {"current": current, "turn": start_turn, "history": history, "usage": usage}
        for turn in range(start_turn, settings.MAX_AGENT_TURNS):
            self._loop_ctx.update({"current": current, "turn": turn, "history": history, "usage": usage})
            await self._check_cancel()
            if reason := await self.pause_ctl.pause_requested():
                await self._do_pause(db, run, current, turn, history,
                                     paused_from=paused_from, reason=reason, usage=usage)
                return RunOutcome.PAUSED

            await StateService.checkpoint(db, self.tenant_id, self.run_id,
                                          current_agent=current.name, turn=turn,
                                          history=history, seq=self.seq,
                                          usage_partial=usage.snapshot(),
                                          pending_tool_call=self._pending_approval,
                                          commit=False)
            await db.commit()
            paused_from = "running"

            est_p, est_c = router.estimate_tokens(history)
            est_cost = router.estimate_cost_usd("", current.model, est_p, est_c)
            try:
                await BudgetGuard.reserve(self.tenant_id, self.run_id, est_cost, policy)
            except BudgetExceeded as e:
                await self.emit(db, "run.budget_exceeded",
                                {"scope": e.scope, "used": e.used, "limit": e.limit})
                raise GuardrailBlocked(str(e))

            schemas = tools.schemas(only=current.tool_names) \
                      + agents.virtual_tool_schemas(current)
            with tracer.start_as_current_span("model.inference") as sp:
                sp.set_attribute("agent.name", current.name)

                async def on_delta(text):
                    await self.emit(db, "model.delta",
                                    {"agent": current.name, "text": text})

                async def on_provider(name, est):
                    await self.emit(db, "model.provider",
                                    {"provider": name, "model": current.model,
                                     "est_cost_usd": round(est, 6)})

                result = await router.chat(current.model, history, schemas,
                                           on_delta=on_delta, on_provider=on_provider)
            usage.add_tokens(result.usage, model=current.model,
                             provider=result.provider, cost_usd=result.cost_usd)
            await usage.check_token_quota(policy)
            await BudgetGuard.settle(self.tenant_id, self.run_id, est_cost, result.cost_usd)
            await CostTimeseries.record(self.tenant_id, result.cost_usd)

            if not result.tool_calls:
                await self._finish(db, run, result.content, usage)
                return RunOutcome.COMPLETED

            history.append(result.as_message())
            for call in result.tool_calls:
                await self._check_cancel()
                target = agents.parse_handoff(call.name)
                if target:
                    current = agents.get(target)
                    await self.emit(db, "agent.handoff",
                                    {"to": target, "reason": call.args.get("reason", "")})
                    history.append({"role": "tool", "tool_call_id": call.id,
                                    "content": json.dumps({"handoff": target, "ok": True})})
                    continue
                sub = agents.parse_agent_tool(call.name)
                if sub:
                    output = await self._run_sub_agent(
                        db, agents.get(sub), tools, policy, usage,
                        call.args.get("question", ""))
                    history.append({"role": "tool", "tool_call_id": call.id, "content": output})
                    continue
                output = await self._execute_tool(db, run, tools, policy, usage, call)
                history.append({"role": "tool", "tool_call_id": call.id, "content": output})

        await self._fail(db, run, "max agent turns exceeded", usage)
        return RunOutcome.FAILED

    async def _do_pause(self, db, run, current, turn, history, *, paused_from, reason, usage):
        cp = Checkpoint(run_id=self.run_id, tenant_id=self.tenant_id, data={
            "messages": history, "history": history,
            "current_agent": current.name, "iteration": turn, "turn": turn,
            "seq": self.seq, "usage_partial": usage.snapshot(),
            "pending_approval": self._pending_approval, "paused_from": paused_from,
        }, version=0)
        existing = await load_checkpoint(db, self.run_id)
        if existing:
            cp.version = existing.version
        await save_checkpoint(db, cp, commit=False)
        try:
            await transition(db, self.run_id, paused_from, "paused", reason=reason, commit=False)
        except StaleTransition:
            refreshed = await db.get(Run, self.run_id)
            if refreshed and refreshed.status.value in TERMINAL:
                return
            raise
        await self.emit(db, "run.paused", {"reason": reason, "paused_from": paused_from,
                                           "iteration": turn})
        await db.commit()
        log.info("run %s paused at iteration %d (%s)", self.run_id, turn, reason)

    async def _restore_from_checkpoint(self, db, run, agents, usage):
        cp = await load_checkpoint(db, self.run_id)
        if cp is None:
            return None
        d = cp.data
        history = d.get("messages") or d.get("history") or []
        turn = d.get("iteration", d.get("turn", 0))
        self.seq = d.get("seq", 0)
        agent_name = d.get("current_agent", agents.default_name)
        current = agents.get(agent_name)
        partial = d.get("usage_partial") or {}
        if partial:
            usage.restore(partial)
        self._pending_approval = d.get("pending_approval")
        await self.emit(db, "run.resumed", {"iteration": turn})
        return current, turn, history

    async def _execute_tool(self, db, run, tools, policy, usage, call) -> str:
        await self.emit(db, "tool.call", {"id": call.id, "name": call.name, "args": call.args})
        try:
            await Guardrails.check_tool_call(policy, call)
            PolicyService.check_tool_allowed(policy, call.name)
        except GuardrailBlocked as e:
            await self.emit(db, "tool.blocked", {"name": call.name, "reason": str(e)})
            return json.dumps({"error": f"blocked by guardrail: {e}"})
        if tools.requires_approval(call.name, policy):
            approved = await self._request_approval(db, run, call)
            if approved is None:
                return json.dumps({"error": "paused during approval"})
            if not approved:
                return json.dumps({"error": "rejected or expired by approver"})
        ctx = ToolContext(tenant_id=self.tenant_id, run_id=self.run_id,
                          session_id=run.session_id, db=db, emit=self.emit, usage=usage,
                          workspace_id=self.workspace_id, user_id=run.user_id)
        with tracer.start_as_current_span(f"tool.{call.name}"):
            output = await tools.execute(ctx, call)
        usage.add_tool_call(call.name)
        await self.emit(db, "tool.result", {"id": call.id, "name": call.name,
                                            "output": output[:4000]})
        return output

    async def _request_approval(self, db, run, call):
        import asyncio
        import time

        approval = ApprovalRequest(
            tenant_id=self.tenant_id, run_id=self.run_id,
            tool_call_id=call.id, tool_name=call.name, tool_args=call.args,
            requested_by=run.user_id)
        db.add(approval)
        await db.flush()
        self._pending_approval = {"approval_id": approval.id,
                                  "tool_call": {"id": call.id, "name": call.name,
                                                "args": call.args}}
        try:
            await transition(db, self.run_id, "running", "awaiting_approval",
                             reason="approval", commit=False)
        except StaleTransition:
            pass
        await self.emit(db, "approval.required",
                        {"approval_id": approval.id, "tool": call.name, "args": call.args})
        await db.commit()

        key = ApprovalService._key(self.tenant_id, self.run_id, call.id)
        deadline = time.monotonic() + 3600
        approved = False
        decided = False
        while time.monotonic() < deadline:
            if reason := await self.pause_ctl.pause_requested():
                ctx = self._loop_ctx
                await self._do_pause(
                    db, run, ctx.get("current"), ctx.get("turn", 0), ctx.get("history", []),
                    paused_from="awaiting_approval", reason=reason,
                    usage=ctx.get("usage"))
                return None
            val = await redis_client.get(key)
            if val:
                approved = json.loads(val)["approved"]
                decided = True
                break
            pending = await db.get(ApprovalRequest, approval.id)
            if pending and pending.status != ApprovalStatus.pending:
                approved = pending.status == ApprovalStatus.approved
                decided = True
                break
            await asyncio.sleep(5)

        if not decided:
            approved = False

        try:
            await transition(db, self.run_id, "awaiting_approval", "running",
                             reason="approval decided", commit=False)
        except StaleTransition:
            pass
        self._pending_approval = None
        await self.emit(db, "approval.decided",
                        {"approval_id": approval.id, "approved": approved})
        await db.commit()
        return approved

    async def _run_sub_agent(self, db, agent, tools, policy, usage, question, max_turns=8) -> str:
        await self.emit(db, "agent.tool.start", {"agent": agent.name, "question": question})
        sub_history = [{"role": "system", "content": agent.instructions},
                       {"role": "user", "content": question}]
        router = ModelRouter.get()
        for _ in range(max_turns):
            result = await router.chat(agent.model, sub_history,
                                       tools.schemas(only=agent.tool_names))
            usage.add_tokens(result.usage, model=agent.model,
                             provider=result.provider, cost_usd=result.cost_usd)
            if not result.tool_calls:
                await self.emit(db, "agent.tool.end",
                                {"agent": agent.name, "answer": result.content[:1000]})
                return result.content
            sub_history.append(result.as_message())
            for call in result.tool_calls:
                run = await db.get(Run, self.run_id)
                output = await self._execute_tool(db, run, tools, policy, usage, call)
                sub_history.append({"role": "tool", "tool_call_id": call.id, "content": output})
        return json.dumps({"error": "sub-agent max turns exceeded"})

    async def _finish(self, db, run, content, usage):
        msg = Message(tenant_id=self.tenant_id, session_id=run.session_id,
                      run_id=self.run_id, role="assistant",
                      content={"text": content})
        db.add(msg)
        await db.flush()
        rows = (await db.execute(select(Message)
            .where(Message.session_id == run.session_id)
            .order_by(Message.created_at))).scalars().all()
        await MemoryService.update_session_summary(
            db,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            session_id=run.session_id,
            messages=[
                {"id": m.id, "role": m.role, "content": m.content}
                for m in rows if m.role in ("user", "assistant")
            ],
        )
        await MemoryService.capture_candidates(
            db,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            session_id=run.session_id,
            run_id=self.run_id,
            user_id=run.user_id,
            messages=[
                {"id": m.id, "role": m.role, "content": m.content}
                for m in rows[-6:]
            ],
        )
        try:
            await transition(db, self.run_id, run.status.value, "completed",
                             reason="done", commit=False)
        except StaleTransition:
            run.status = RunStatus.completed
        await self.emit(db, "run.completed",
                        {"content": content, "usage": usage.snapshot()})
        await db.commit()

    async def _fail(self, db, run, reason, usage):
        try:
            await transition(db, self.run_id, run.status.value, "failed",
                             reason=reason[:200], extra={"error": reason[:2000]},
                             commit=False)
        except StaleTransition:
            run.status, run.error = RunStatus.failed, reason[:2000]
        await self.emit(db, "run.failed", {"reason": reason, "usage": usage.snapshot()})
        await db.commit()

    async def _build_history(self, db, run, agent) -> list:
        skills = (await db.execute(select(Skill).where(
            Skill.tenant_id == self.tenant_id, Skill.enabled == True))).scalars().all()
        system = DEFAULT_SYSTEM_PROMPT + "\n" + agent.instructions
        if run.agent_config.get("system_prompt"):
            system += "\n" + run.agent_config["system_prompt"]
        summary = await MemoryService.get_session_summary(
            db, tenant_id=self.tenant_id, session_id=run.session_id)
        for s in skills:
            system += f"\n\n## Skill: {s.name}\n{s.instructions}"
        rows = (await db.execute(select(Message)
            .where(Message.session_id == run.session_id)
            .order_by(Message.created_at.desc()).limit(40))).scalars().all()
        msgs = [{"role": m.role, "content": m.content.get("text", "")}
                for m in reversed(rows) if m.role in ("user", "assistant")]
        query = "\n".join(m["content"] for m in msgs[-4:])
        memories = await MemoryService.search(
            db,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            session_id=run.session_id,
            run_id=self.run_id,
            user_id=run.user_id,
            query=query,
            limit=8,
        )
        memory_block = MemoryService.format_for_prompt(memories, summary)
        if memory_block:
            system += "\n\n" + memory_block
        return [{"role": "system", "content": system}] + msgs
