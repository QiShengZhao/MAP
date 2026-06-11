from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.infra.db import get_db
from app.api.deps import get_auth, require_admin
from app.platform_services.policy import PolicyService

router = APIRouter(prefix="/v1/policy", tags=["policy"])

@router.get("")
async def get_policy(auth=Depends(get_auth), db=Depends(get_db)):
    p = await PolicyService.get(db, auth.tenant_id)
    return {"allowed_tools": p.allowed_tools,
            "approval_required_tools": p.approval_required_tools,
            "blocked_domains": p.blocked_domains,
            "max_concurrent_runs": p.max_concurrent_runs,
            "max_tokens_per_day": p.max_tokens_per_day,
            "max_cost_per_day_usd": p.max_cost_per_day_usd,
            "max_cost_per_run_usd": p.max_cost_per_run_usd}

class PolicyReq(BaseModel):
    allowed_tools: list[str] = []
    approval_required_tools: list[str] = []
    blocked_domains: list[str] = []
    max_concurrent_runs: int = 5
    max_tokens_per_day: int = 1_000_000
    max_cost_per_day_usd: float = 50.0
    max_cost_per_run_usd: float = 2.0

@router.put("")
async def update_policy(req: PolicyReq, auth=Depends(require_admin),
                        db=Depends(get_db)):
    p = await PolicyService.get(db, auth.tenant_id)
    for k, v in req.model_dump().items():
        setattr(p, k, v)
    await db.commit()
    return {"ok": True}