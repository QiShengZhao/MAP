from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.infra.db import get_db
from app.api.deps import require_admin, get_auth
from app.domain.models import RiskRule, RiskIncident
from app.risk.expression import SafeExpression, ExpressionError
from app.risk.engine import RuleEngine

router = APIRouter(prefix="/v1/risk", tags=["risk"])
VALID_ACTIONS = ("throttle", "flag", "notify", "pause_tenant")

class RuleReq(BaseModel):
    name: str
    description: str = ""
    condition: str
    actions: list[dict]
    priority: int = 100
    cooldown_seconds: int = 600
    enabled: bool = True

@router.post("/rules")
async def create_rule(req: RuleReq, auth=Depends(require_admin),
                      db=Depends(get_db)):
    try:
        SafeExpression(req.condition)
    except ExpressionError as e:
        raise HTTPException(400, f"invalid condition: {e}")
    for a in req.actions:
        if a.get("type") not in VALID_ACTIONS:
            raise HTTPException(400, f"unknown action: {a.get('type')}")
    rule = RiskRule(tenant_id=auth.tenant_id, name=req.name,
                    description=req.description, condition=req.condition,
                    actions=req.actions, priority=req.priority,
                    cooldown_seconds=req.cooldown_seconds,
                    enabled=req.enabled, updated_by=auth.user_id)
    db.add(rule)
    await db.commit()
    await RuleEngine.signal_reload()
    return {"id": rule.id}

@router.put("/rules/{rule_id}")
async def update_rule(rule_id: str, req: RuleReq,
                      auth=Depends(require_admin), db=Depends(get_db)):
    rule = await db.get(RiskRule, rule_id)
    if not rule or rule.tenant_id != auth.tenant_id:
        raise HTTPException(404)
    try:
        SafeExpression(req.condition)
    except ExpressionError as e:
        raise HTTPException(400, f"invalid condition: {e}")
    for k in ("name", "description", "condition", "actions", "priority",
              "cooldown_seconds", "enabled"):
        setattr(rule, k, getattr(req, k))
    rule.version += 1
    rule.updated_by = auth.user_id
    await db.commit()
    await RuleEngine.signal_reload()
    return {"ok": True, "version": rule.version}

class DryRunReq(BaseModel):
    condition: str
    metrics: dict

@router.post("/rules/dry-run")
async def dry_run(req: DryRunReq, auth=Depends(require_admin)):
    try:
        return {"matched": SafeExpression(req.condition).evaluate(req.metrics)}
    except ExpressionError as e:
        raise HTTPException(400, str(e))

@router.get("/incidents")
async def list_incidents(limit: int = 50, auth=Depends(get_auth),
                         db=Depends(get_db)):
    rows = (await db.execute(select(RiskIncident)
        .where(RiskIncident.tenant_id == auth.tenant_id)
        .order_by(RiskIncident.created_at.desc()).limit(limit))).scalars().all()
    return [{"rule": i.rule_name, "metrics": i.metrics,
             "actions": i.actions_taken,
             "at": i.created_at.isoformat()} for i in rows]