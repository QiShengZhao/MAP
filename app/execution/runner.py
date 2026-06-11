import json, logging
from datetime import datetime
from sqlalchemy import select
from app.config import settings
from app.infra.db import tenant_session
from app.infra.redis_client import redis_client
from app.domain.models import (Run, RunStatus, Message, RunEvent,
                               ApprovalRequest, Skill)
from app.runtime.model_router import ModelRouter
from app.runtime.tools import ToolRegistry, ToolContext
from app.runtime.guardrails import Guardrails, GuardrailBlocked
from app.runtime.approval import ApprovalService
from app.runtime.sandbox import SandboxManager
from app.runtime.agents import AgentRegistry
from app.runtime.state import StateService
from app.runtime.budget import BudgetGuard, BudgetExceeded
from app.platform_services.usage import UsageMeter
from app.platform_services.policy import PolicyService
from app.platform_services.cost_timeseries import CostTimeseries
from app.observability.tracing import tracer

log = logging.getLogger("runner")

DEFAULT_SYSTEM_PROMPT = """You are an AI agent on a multi-tenant platform.
Rules:
1. You only make DECISIONS; all actions must go through tools.
2. Never fabricate tool results. If a tool fails, report honestly.
3. Commands run inside an isolated sandbox; produced files become artifacts.
"""

class RunCancelled(Exception): pass

class Runner:
    def __init__(self, tenant_id, run_id, workspace_id=""):
        self.tenant_id, self.run_id, self.workspace_id = \
            tenant_id, run_id, workspace_id
        self.seq = 0

    async def emit(self, db, type_, payload):
        """事件发布：kafka 模式只写 Kafka；redis 模式双写 DB + pub/sub。"""
        from app.config import settings
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

    async def _check_cancel(self):
        if await redis_client.get(f"cancel:run:{self.run_id}"):
            raise RunCancelled()

    async def execute(self):
        with tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("tenant.id", self.tenant_id)
            span.set_attribute("run.id", self.run_id)
            async with tenant_session(self.tenant_id) as db:
                run = await db.get(Run, self.run_id)
                if not run or run.status not in (RunStatus.queued,
                                                 RunStatus.running):
                    return
                run.status, run.started_at = RunStatus.running, datetime.utcnow()
                run.trace_id = format(span.get_span_context().trace_id, "032x")
                await self.emit(db, "run.started", {"run_id": self.run_id})
                await db.commit()

                policy = await PolicyService.get(db, self.tenant_id)
                usage = UsageMeter(self.tenant_id, self.workspace_id, self.run_id)
                tools = ToolRegistry.for_tenant(policy)
                agents = await AgentRegistry.load(db, self.tenant_id)

                # 断点恢复
                state = await StateService.load(db, self.tenant_id, self.run_id)
                if state:
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
                    await self._agent_loop(db, run, agents, current, tools,
                                           policy, usage, history, turn)
                    await StateService.clear(db, self.tenant_id, self.run_id)
                except RunCancelled:
                    run.status = RunStatus.cancelled
                    await self.emit(db, "run.cancelled", {})
                except GuardrailBlocked as e:
                    await self._fail(db, run, f"guardrail blocked: {e}", usage)
                except Exception as e:
                    log.exception("run %s failed", self.run_id)
                    await self._fail(db, run, str(e), usage)
                finally:
                    await SandboxManager.release_for_run(
                        self.tenant_id, run.session_id, usage)
                    await usage.flush(db)
                    run.usage = usage.snapshot()
                    run.finished_at = datetime.utcnow()
                    await db.commit()

    async def _agent_loop(self, db, run, agents, current, tools,
                          policy, usage, history, start_turn=0):
        router = ModelRouter.get()
        for turn in range(start_turn, settings.MAX_AGENT_TURNS):
            await self._check_cancel()
            # 检查点（崩溃恢复）
            await StateService.checkpoint(db, self.tenant_id, self.run_id,
                current_agent=current.name, turn=turn, history=history)
            await db.commit()

            # 预算预占
            est_p, est_c = router.estimate_tokens(history)
            est_cost = router.estimate_cost_usd("", current.model, est_p, est_c)
            try:
                await BudgetGuard.reserve(self.tenant_id, self.run_id,
                                          est_cost, policy)
            except BudgetExceeded as e:
                await self.emit(db, "run.budget_exceeded",
                                {"scope": e.scope, "used": e.used,
                                 "limit": e.limit})
                raise GuardrailBlocked(str(e))

            # 模型推理（成本选路 + failover）
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
                                           on_delta=on_delta,
                                           on_provider=on_provider)
            usage.add_tokens(result.usage, model=current.model,
                             provider=result.provider, cost_usd=result.cost_usd)
            await usage.check_token_quota(policy)
            await BudgetGuard.settle(self.tenant_id, self.run_id,
                                     est_cost, result.cost_usd)
            await CostTimeseries.record(self.tenant_id, result.cost_usd)

            if not result.tool_calls:
                await self._finish(db, run, result.content, usage)
                return

            history.append(result.as_message())
            for call in result.tool_calls:
                await self._check_cancel()
                # Handoff
                target = agents.parse_handoff(call.name)
                if target:
                    current = agents.get(target)
                    await self.emit(db, "agent.handoff",
                                    {"to": target,
                                     "reason": call.args.get("reason", "")})
                    history.append({"role": "tool", "tool_call_id": call.id,
                                    "content": json.dumps(
                                        {"handoff": target, "ok": True})})
                    continue
                # Agent as Tool
                sub = agents.parse_agent_tool(call.name)
                if sub:
                    output = await self._run_sub_agent(
                        db, agents.get(sub), tools, policy, usage,
                        call.args.get("question", ""))
                    history.append({"role": "tool", "tool_call_id": call.id,
                                    "content": output})
                    continue
                # 普通工具
                output = await self._execute_tool(db, run, tools, policy,
                                                  usage, call)
                history.append({"role": "tool", "tool_call_id": call.id,
                                "content": output})
        await self._fail(db, run, "max agent turns exceeded", usage)

    async def _execute_tool(self, db, run, tools, policy, usage, call) -> str:
        await self.emit(db, "tool.call",
                        {"id": call.id, "name": call.name, "args": call.args})
        try:
            Guardrails.check_tool_call(policy, call)
            PolicyService.check_tool_allowed(policy, call.name)
        except GuardrailBlocked as e:
            await self.emit(db, "tool.blocked",
                            {"name": call.name, "reason": str(e)})
            return json.dumps({"error": f"blocked by guardrail: {e}"})
        # 高风险审批（HITL）
        if tools.requires_approval(call.name, policy):
            approved = await self._request_approval(db, run, call)
            if not approved:
                return json.dumps({"error": "rejected or expired by approver"})
        ctx = ToolContext(tenant_id=self.tenant_id, run_id=self.run_id,
                          session_id=run.session_id, db=db, emit=self.emit,
                          usage=usage)
        with tracer.start_as_current_span(f"tool.{call.name}"):
            output = await tools.execute(ctx, call)
        usage.add_tool_call(call.name)
        await self.emit(db, "tool.result",
                        {"id": call.id, "name": call.name,
                         "output": output[:4000]})
        return output

    async def _run_sub_agent(self, db, agent, tools, policy, usage,
                             question, max_turns=8) -> str:
        await self.emit(db, "agent.tool.start",
                        {"agent": agent.name, "question": question})
        sub_history = [{"role": "system", "content": agent.instructions},
                       {"role": "user", "content": question}]
        router = ModelRouter.get()
        for _ in range(max_turns):
            result = await router.chat(agent.model, sub_history,
                                       tools.schemas(only=agent.tool_names))
            usage.add_tokens(result.usage, model=agent.model,
                             provider=result.provider,
                             cost_usd=result.cost_usd)
            if not result.tool_calls:
                await self.emit(db, "agent.tool.end",
                                {"agent": agent.name,
                                 "answer": result.content[:1000]})
                return result.content
            sub_history.append(result.as_message())
            for call in result.tool_calls:
                run = await db.get(Run, self.run_id)
                output = await self._execute_tool(db, run, tools, policy,
                                                  usage, call)
                sub_history.append({"role": "tool", "tool_call_id": call.id,
                                    "content": output})
        return json.dumps({"error": "sub-agent max turns exceeded"})

    async def _request_approval(self, db, run, call) -> bool:
        approval = ApprovalRequest(
            tenant_id=self.tenant_id, run_id=self.run_id,
            tool_call_id=call.id, tool_name=call.name, tool_args=call.args,
            requested_by=run.user_id)
        db.add(approval)
        run.status = RunStatus.awaiting_approval
        await self.emit(db, "approval.required",
                        {"approval_id": approval.id, "tool": call.name,
                         "args": call.args})
        await db.commit()
        approved = await ApprovalService.wait(self.tenant_id, self.run_id,
                                              call.id, timeout=3600)
        run.status = RunStatus.running
        await self.emit(db, "approval.decided",
                        {"approval_id": approval.id, "approved": approved})
        await db.commit()
        return approved

    async def _finish(self, db, run, content, usage):
        db.add(Message(tenant_id=self.tenant_id, session_id=run.session_id,
                       run_id=self.run_id, role="assistant",
                       content={"text": content}))
        run.status = RunStatus.completed
        await self.emit(db, "run.completed",
                        {"content": content, "usage": usage.snapshot()})
        await db.commit()

    async def _fail(self, db, run, reason, usage):
        run.status, run.error = RunStatus.failed, reason[:2000]
        await self.emit(db, "run.failed",
                        {"reason": reason, "usage": usage.snapshot()})
        await db.commit()

    async def _build_history(self, db, run, agent) -> list:
        skills = (await db.execute(select(Skill).where(
            Skill.tenant_id == self.tenant_id,
            Skill.enabled == True))).scalars().all()
        system = DEFAULT_SYSTEM_PROMPT + "\n" + agent.instructions
        if run.agent_config.get("system_prompt"):
            system += "\n" + run.agent_config["system_prompt"]
        for s in skills:
            system += f"\n\n## Skill: {s.name}\n{s.instructions}"
        rows = (await db.execute(select(Message)
            .where(Message.session_id == run.session_id)
            .order_by(Message.created_at.desc()).limit(40))).scalars().all()
        msgs = [{"role": m.role, "content": m.content.get("text", "")}
                for m in reversed(rows) if m.role in ("user", "assistant")]
        return [{"role": "system", "content": system}] + msgs