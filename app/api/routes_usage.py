from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from app.infra.db import get_db
from app.api.deps import get_auth
from app.domain.models import UsageRecord

router = APIRouter(prefix="/v1/usage", tags=["usage"])

@router.get("/summary")
async def usage_summary(days: int = 30, auth=Depends(get_auth), db=Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=days)
    rows = (await db.execute(
        select(UsageRecord.kind, func.sum(UsageRecord.quantity))
        .where(UsageRecord.tenant_id == auth.tenant_id,
               UsageRecord.created_at >= since)
        .group_by(UsageRecord.kind))).all()
    return {kind: int(total or 0) for kind, total in rows}

@router.get("/budget")
async def budget_status(auth=Depends(get_auth), db=Depends(get_db)):
    from app.platform_services.policy import PolicyService
    from app.runtime.budget import BudgetGuard
    policy = await PolicyService.get(db, auth.tenant_id)
    return await BudgetGuard.status(auth.tenant_id, policy)

@router.get("/budget/forecast")
async def budget_forecast(auth=Depends(get_auth), db=Depends(get_db)):
    from app.platform_services.policy import PolicyService
    from app.platform_services.burn_monitor import BurnRateMonitor
    from app.platform_services.cost_timeseries import CostTimeseries
    policy = await PolicyService.get(db, auth.tenant_id)
    series = await CostTimeseries.recent_minutes(auth.tenant_id, 30)
    report = await BurnRateMonitor.analyze_tenant(auth.tenant_id, policy)
    return {"series_30min": series, "forecast": report or {"level": "ok"}}