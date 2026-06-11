import logging
import stripe
from sqlalchemy import select, func
from app.config import settings
from app.domain.models import TenantMember, BillingAccount

log = logging.getLogger("seats")

class SeatLimitExceeded(Exception): pass

class SeatService:
    @staticmethod
    async def member_count(db, tenant_id):
        return (await db.execute(select(func.count())
            .select_from(TenantMember)
            .where(TenantMember.tenant_id == tenant_id))).scalar()

    @staticmethod
    def seat_capacity(acc):
        if not acc or acc.plan == "free":
            return settings.SEATS_INCLUDED_IN_BASE
        return settings.SEATS_INCLUDED_IN_BASE + acc.seats_purchased

    @classmethod
    async def check_can_add_member(cls, db, tenant_id):
        acc = await db.get(BillingAccount, tenant_id)
        if not acc:
            return
        count = await cls.member_count(db, tenant_id)
        if count >= cls.seat_capacity(acc):
            raise SeatLimitExceeded(
                f"seat limit reached ({count}/{cls.seat_capacity(acc)})")

    @classmethod
    async def sync_seats(cls, db, tenant_id, auto_expand=True):
        acc = await db.get(BillingAccount, tenant_id)
        if not acc or not acc.si_seats:
            return
        count = await cls.member_count(db, tenant_id)
        needed = max(0, count - settings.SEATS_INCLUDED_IN_BASE)
        if needed == acc.seats_purchased:
            return
        if needed > acc.seats_purchased and not auto_expand:
            raise SeatLimitExceeded("auto seat expansion disabled")
        stripe.SubscriptionItem.modify(
            acc.si_seats, quantity=needed,
            proration_behavior="create_prorations")
        acc.seats_purchased = needed
        await db.flush()