import asyncio, hashlib, logging, math, time
from datetime import datetime
import stripe
from sqlalchemy import select, func
from app.config import settings
from app.infra.db import SessionLocal
from app.domain.models import BillingAccount, UsageRecord, UsageReportCursor

log = logging.getLogger("billing-reporter")
stripe.api_key = settings.STRIPE_API_KEY

UNIT_SIZE = {"tokens": 1000, "sandbox_seconds": 60}
KIND_TO_ITEM = {"tokens": "si_tokens", "sandbox_seconds": "si_sandbox"}


def billable_quantity(kind, quantity):
    return math.ceil(quantity / UNIT_SIZE[kind])


def usage_batch_key(tenant_id, kind, start, end=None):
    # The cursor start uniquely identifies a batch and remains stable on retry.
    raw = f"{tenant_id}:{kind}:{start.isoformat()}".encode()
    return "usage:" + hashlib.sha256(raw).hexdigest()


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
        qty = billable_quantity(kind, int(total))
        stripe.SubscriptionItem.create_usage_record(
            item_id, quantity=qty, action="increment",
            timestamp=int(time.time()),
            idempotency_key=usage_batch_key(
                acc.tenant_id, kind, cursor.last_reported_at, watermark))
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
