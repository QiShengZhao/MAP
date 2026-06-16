"""风控规则 CRUD + dry-run + 事件查询 + 租户暂停管理 + Run 级暂停/恢复。"""
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, or_, select

from app.api.deps import AuthContext, get_auth, get_db, require_role
from app.domain.models import RiskIncident, RiskRule, Run, RunStatus
from app.eventbus.risk_consumer import EXPECTED_METRICS
from app.infra.redis_client import get_redis
from app.risk.engine import RiskEngine, VALID_ACTIONS
from app.risk.expression import ExpressionError, compile_expr, evaluate_compiled

log = logging.getLogger("api.risk")
router = APIRouter(prefix="/v1/risk", tags=["risk"])
admin = Depends(require_role("owner", "admin"))


def utcnow_naive() -> datetime:
    return datetime.utcnow()


class RuleIn(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    description: str = ""
    expression: str = Field(max_length=2000)
    action: str
    action_params: dict = {}
    cooldown_seconds: int = Field(default=300, ge=10, le=86400)
    severity: str = "warning"
    enabled: bool = True
    platform_scope: bool = False

    @field_validator("action")
    @classmethod
    def action_valid(cls, v):
        if v not in VALID_ACTIONS:
            raise ValueError(f"action must be one of {sorted(VALID_ACTIONS)}")
        return v

    @field_validator("severity")
    @classmethod
    def severity_valid(cls, v):
        if v not in ("info", "warning", "critical"):
            raise ValueError("severity must be info/warning/critical")
        return v

    @field_validator("expression")
    @classmethod
    def expression_compiles(cls, v):
        try:
            compile_expr(v)
        except ExpressionError as e:
            raise ValueError(f"expression invalid: {e}")
        return v

    @model_validator(mode="after")
    def action_params_valid(self):
        p = self.action_params
        if self.action == "throttle":
            mcr = p.get("max_concurrent_runs", 1)
            dur = p.get("duration_seconds", 600)
            if not (isinstance(mcr, int) and 0 <= mcr <= 100):
                raise ValueError("throttle.max_concurrent_runs must be int 0~100")
            if not (isinstance(dur, int) and 60 <= dur <= 86400):
                raise ValueError("throttle.duration_seconds must be 60~86400")
        if self.action == "pause":
            dur = p.get("duration_seconds", 1800)
            if not (isinstance(dur, int) and (dur == 0 or 60 <= dur <= 86400)):
                raise ValueError("pause.duration_seconds must be 0(manual) or 60~86400")
            if dur > 0 and self.cooldown_seconds > dur:
                raise ValueError("cooldown_seconds must be <= pause duration_seconds")
        if self.action == "notify":
            url = p.get("webhook_url", "")
            if url and not url.startswith("https://"):
                raise ValueError("webhook_url must be https")
        return self


class RuleOut(BaseModel):
    id: str
    name: str
    description: str
    expression: str
    action: str
    action_params: dict
    cooldown_seconds: int
    severity: str
    enabled: bool
    platform_scope: bool
    created_at: datetime
    updated_at: datetime | None

    @classmethod
    def from_row(cls, r: RiskRule) -> "RuleOut":
        return cls(id=str(r.id), name=r.name, description=r.description or "",
                   expression=r.expression, action=r.action or "flag",
                   action_params=r.action_params or {},
                   cooldown_seconds=r.cooldown_seconds, severity=r.severity or "warning",
                   enabled=r.enabled, platform_scope=r.tenant_id is None,
                   created_at=r.created_at, updated_at=r.updated_at)


class DryRunIn(BaseModel):
    expression: str | None = None
    rule_id: str | None = None
    contexts: list[dict] = Field(min_length=1, max_length=50)
    use_recent_windows: int = Field(default=0, ge=0, le=60)

    @model_validator(mode="after")
    def one_target(self):
        if bool(self.expression) == bool(self.rule_id):
            raise ValueError("exactly one of expression / rule_id required")
        return self


def auth_is_platform_admin(auth: AuthContext) -> bool:
    return auth.is_platform_admin


async def _get_rule_or_404(db, rule_id: str, auth: AuthContext) -> RiskRule:
    rule = await db.scalar(select(RiskRule).where(RiskRule.id == rule_id))
    if not rule:
        raise HTTPException(404, "rule not found")
    if rule.tenant_id is None and not auth_is_platform_admin(auth):
        if auth.role not in ("owner", "admin"):
            raise HTTPException(403, "platform rule requires platform admin")
    elif rule.tenant_id and rule.tenant_id != auth.tenant_id:
        raise HTTPException(404, "rule not found")
    return rule


def _check_platform_scope(body: RuleIn, auth: AuthContext) -> str | None:
    if body.platform_scope:
        if not auth_is_platform_admin(auth):
            raise HTTPException(403, "platform-scope rules require platform admin")
        return None
    return auth.tenant_id


@router.get("/rules", response_model=list[RuleOut])
async def list_rules(auth: AuthContext = admin, db=Depends(get_db),
                     enabled: bool | None = Query(None),
                     include_platform: bool = Query(True)):
    cond = [or_(RiskRule.tenant_id.is_(None), RiskRule.tenant_id == auth.tenant_id)] \
        if include_platform else [RiskRule.tenant_id == auth.tenant_id]
    q = select(RiskRule).where(*cond)
    if enabled is not None:
        q = q.where(RiskRule.enabled == enabled)
    rows = (await db.scalars(q.order_by(RiskRule.created_at.desc()))).all()
    return [RuleOut.from_row(r) for r in rows]


@router.post("/rules", response_model=RuleOut, status_code=201)
async def create_rule(body: RuleIn, auth: AuthContext = admin, db=Depends(get_db)):
    tenant_id = _check_platform_scope(body, auth)
    dup = await db.scalar(select(RiskRule).where(
        RiskRule.tenant_id == tenant_id, RiskRule.name == body.name))
    if dup:
        raise HTTPException(409, f"rule name '{body.name}' already exists")
    count = await db.scalar(select(func.count()).select_from(RiskRule)
                            .where(RiskRule.tenant_id == tenant_id))
    if count and count >= 50:
        raise HTTPException(422, "rule limit reached (50 per tenant)")

    rule = RiskRule(tenant_id=tenant_id, name=body.name, description=body.description,
                    condition=body.expression, action=body.action,
                    action_params=body.action_params,
                    cooldown_seconds=body.cooldown_seconds,
                    severity=body.severity, enabled=body.enabled,
                    created_by=auth.user_id, updated_by=auth.user_id)
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    await RiskEngine.publish_reload()
    return RuleOut.from_row(rule)


@router.put("/rules/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: str, body: RuleIn,
                      auth: AuthContext = admin, db=Depends(get_db)):
    rule = await _get_rule_or_404(db, rule_id, auth)
    if body.platform_scope != (rule.tenant_id is None):
        raise HTTPException(422, "cannot change rule scope; delete and recreate")
    rule.name = body.name
    rule.description = body.description
    rule.condition = body.expression
    rule.action = body.action
    rule.action_params = body.action_params
    rule.cooldown_seconds = body.cooldown_seconds
    rule.severity = body.severity
    rule.enabled = body.enabled
    rule.updated_at = utcnow_naive()
    rule.updated_by = auth.user_id
    await db.commit()
    await db.refresh(rule)
    await RiskEngine.publish_reload()
    return RuleOut.from_row(rule)


@router.patch("/rules/{rule_id}/toggle", response_model=RuleOut)
async def toggle_rule(rule_id: str, enabled: bool,
                      auth: AuthContext = admin, db=Depends(get_db)):
    rule = await _get_rule_or_404(db, rule_id, auth)
    rule.enabled = enabled
    rule.updated_at = utcnow_naive()
    await db.commit()
    await db.refresh(rule)
    await RiskEngine.publish_reload()
    return RuleOut.from_row(rule)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: str, auth: AuthContext = admin, db=Depends(get_db)):
    rule = await _get_rule_or_404(db, rule_id, auth)
    await db.delete(rule)
    await db.commit()
    await RiskEngine.publish_reload()
    r = await get_redis()
    async for key in r.scan_iter(f"risk:cooldown:{rule_id}:*", count=100):
        await r.delete(key)


@router.post("/rules/dry-run")
async def dry_run(body: DryRunIn, auth: AuthContext = admin, db=Depends(get_db)):
    if body.rule_id:
        rule = await _get_rule_or_404(db, body.rule_id, auth)
        expression = rule.expression
        compiled = compile_expr(expression)
    else:
        expression = body.expression
        compiled = compile_expr(expression)

    contexts = list(body.contexts)
    if body.use_recent_windows:
        from app.platform_services.cost_timeseries import CostTimeseries
        contexts += await CostTimeseries.recent_metric_windows(
            auth.tenant_id, body.use_recent_windows)

    results = []
    for ctx in contexts:
        full = {m: float(ctx.get(m, 0.0)) for m in EXPECTED_METRICS}
        full["tenant_id"] = auth.tenant_id
        try:
            hit = evaluate_compiled(compiled, full)
            results.append({"context": full, "hit": bool(hit)})
        except ExpressionError as e:
            results.append({"context": full, "hit": None, "error": str(e)})

    hits = sum(1 for x in results if x["hit"])
    return {"expression": expression, "total": len(results), "hits": hits,
            "hit_rate": hits / len(results) if results else 0, "results": results}


@router.get("/incidents")
async def list_incidents(auth: AuthContext = admin, db=Depends(get_db),
                         severity: str | None = None,
                         rule_id: str | None = None,
                         limit: int = Query(50, le=200), offset: int = 0):
    q = select(RiskIncident).where(RiskIncident.tenant_id == auth.tenant_id)
    if severity:
        q = q.where(RiskIncident.severity == severity)
    if rule_id:
        q = q.where(RiskIncident.rule_id == rule_id)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = (await db.scalars(q.order_by(RiskIncident.created_at.desc())
                             .limit(limit).offset(offset))).all()
    return {"total": total, "items": [{
        "id": str(i.id), "rule_name": i.rule_name, "severity": i.severity,
        "action": i.action, "action_executed": i.action_executed,
        "context": i.context or i.metrics, "created_at": i.created_at,
    } for i in rows]}


@router.get("/pause/status")
async def pause_status(auth: AuthContext = admin, db=Depends(get_db)):
    r = await get_redis()
    raw = await r.get(f"risk:paused:{auth.tenant_id}")
    ttl = await r.ttl(f"risk:paused:{auth.tenant_id}") if raw else None
    paused_runs = await db.scalar(select(func.count()).select_from(Run).where(
        Run.tenant_id == auth.tenant_id, Run.status == RunStatus.paused))
    throttle = await r.get(f"risk:throttle:{auth.tenant_id}")
    return {"tenant_paused": bool(raw),
            "pause_info": json.loads(raw) if raw else None,
            "pause_ttl_seconds": ttl if (ttl or 0) > 0 else None,
            "paused_run_count": paused_runs,
            "throttle": json.loads(throttle) if throttle else None}


@router.delete("/pause", status_code=202)
async def unpause_tenant(auth: AuthContext = admin, db=Depends(get_db)):
    r = await get_redis()
    deleted = await r.delete(f"risk:paused:{auth.tenant_id}")
    if not deleted:
        raise HTTPException(404, "tenant is not paused")

    from app.scheduling.scheduler import _requeue_paused_run
    rows = (await db.scalars(select(Run).where(
        Run.tenant_id == auth.tenant_id, Run.status == RunStatus.paused))).all()
    requeued = 0
    for run in rows:
        if await r.exists(f"run:{run.id}:pause"):
            continue
        await _requeue_paused_run(run)
        requeued += 1
    return {"status": "unpaused", "runs_requeued": requeued}


@router.post("/runs/{run_id}/pause", status_code=202)
async def pause_run(run_id: str, reason: str = "manual",
                    auth: AuthContext = admin, db=Depends(get_db)):
    run = await db.scalar(select(Run).where(Run.id == run_id))
    if not run or run.tenant_id != auth.tenant_id:
        raise HTTPException(404, "run not found")
    if run.status not in (RunStatus.running, RunStatus.awaiting_approval):
        raise HTTPException(409, f"run is {run.status.value}, cannot pause")
    r = await get_redis()
    await r.set(f"run:{run_id}:pause", reason, ex=86400)
    await r.publish(f"run:{run_id}:control", json.dumps({"op": "pause"}))
    return {"status": "pause_requested",
            "note": "takes effect at next safe point (iteration boundary)"}


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: str, auth: AuthContext = admin, db=Depends(get_db)):
    run = await db.scalar(select(Run).where(Run.id == run_id))
    if not run or run.tenant_id != auth.tenant_id:
        raise HTTPException(404, "run not found")
    if run.status != RunStatus.paused:
        raise HTTPException(409, f"run is {run.status.value}, not paused")
    r = await get_redis()
    if await r.exists(f"risk:paused:{auth.tenant_id}"):
        raise HTTPException(409, "tenant is paused by risk control; unpause tenant first")
    await r.delete(f"run:{run_id}:pause")
    from app.scheduling.scheduler import _requeue_paused_run
    await _requeue_paused_run(run)
    return {"status": "requeued"}
