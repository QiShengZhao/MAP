import asyncio, logging, time
from datetime import datetime
import stripe
from sqlalchemy import select, func
from app.config import settings
from app.infra.db import SessionLocal
from app.domain.models import BillingAccount, UsageRecord, UsageReportCursor

log = logging.getLogger("billing-reporter")
stripe.api_key = settings.STRIPE_API_KEY

CONVERT = {"tokens": lambda q: max(1, q // 1000),       # 千 token
           "sandbox_seconds": lambda q: max(1, q // 60)} # 分钟
KIND_TO_ITEM = {"tokens": "si_tokens", "sandbox_seconds": "si_sandbox"}

async def report_tenant(db, acc):
    for kind, item_attr in KIND_TO_ITEM.items():
        item_id = getattr(acc, item_attr)
        if not item_id:
            continue
        cursor = await db.get(UsageReportCursor, (acc.tenant_id, kind))
        if cursor is None:
            cursor = UsageReportCursor(tenant_id=acc.tenant_id, kind=kind,
                                       last_reported_at=datetime(2000, 1, 1))
            db.add(cursor)
        watermark = datetime.utcnow()
        total = (await db.execute(select(func.sum(UsageRecord.quantity)).where(
            UsageRecord.tenant_id == acc.tenant_id,
            UsageRecord.kind == kind,
            UsageRecord.created_at > cursor.last_reported_at,
            UsageRecord.created_at <= watermark))).scalar() or 0
        if total <= 0:
            continue
        qty = CONVERT[kind](int(total))
        stripe.SubscriptionItem.create_usage_record(
            item_id, quantity=qty, action="increment",
            timestamp=int(time.time()),
            idempotency_key=f"{acc.tenant_id}:{kind}:{watermark.isoformat()}")
        cursor.last_reported_at = watermark
        log.info("reported tenant=%s kind=%s qty=%d", acc.tenant_id, kind, qty)

async def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            async with SessionLocal() as db:
                accounts = (await db.execute(select(BillingAccount).where(
                    BillingAccount.status.in_(("active", "trialing", "past_due"))
                ))).scalars().all()
                for acc in accounts:
                    try:
                        await report_tenant(db, acc)
                    except Exception:
                        log.exception("report failed tenant=%s", acc.tenant_id)
                await db.commit()
        except Exception:
            log.exception("reporter tick failed")
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())