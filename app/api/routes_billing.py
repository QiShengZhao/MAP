import stripe
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.config import settings
from app.infra import db as db_mod
from app.infra.db import get_db
from app.api.deps import get_auth, require_admin
from app.domain.models import User, BillingAccount
from app.platform_services.billing import BillingService

router = APIRouter(prefix="/v1/billing", tags=["billing"])

@router.get("")
async def billing_status(auth=Depends(get_auth), db=Depends(get_db)):
    acc = await BillingService.get_account(db, auth.tenant_id)
    return {"plan": acc.plan, "status": acc.status,
            "interval": acc.billing_interval,
            "current_period_end": acc.current_period_end.isoformat()
                if acc.current_period_end else None}

class CheckoutReq(BaseModel):
    promo_code: str | None = None
    currency: str | None = None
    interval: str = "month"
    seats: int = 0

@router.post("/checkout")
async def checkout(req: CheckoutReq = CheckoutReq(),
                   auth=Depends(require_admin), db=Depends(get_db)):
    user = await db.get(User, auth.user_id)
    try:
        url = await BillingService.create_checkout(
            db, auth.tenant_id, user.email, promo_code=req.promo_code,
            currency=req.currency, interval=req.interval, seats=req.seats)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.commit()
    return {"checkout_url": url}

@router.post("/portal")
async def portal(auth=Depends(require_admin), db=Depends(get_db)):
    acc = await BillingService.get_account(db, auth.tenant_id)
    if not acc.stripe_customer_id:
        raise HTTPException(400, "no billing account")
    return {"portal_url": await BillingService.create_portal(db, auth.tenant_id)}

class IntervalReq(BaseModel):
    interval: str

@router.post("/interval")
async def switch_interval(req: IntervalReq, auth=Depends(require_admin),
                          db=Depends(get_db)):
    try:
        result = await BillingService.switch_interval(db, auth.tenant_id,
                                                      req.interval)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.commit()
    return result

@router.get("/seats")
async def seat_status(auth=Depends(get_auth), db=Depends(get_db)):
    from app.platform_services.seats import SeatService
    acc = await BillingService.get_account(db, auth.tenant_id)
    used = await SeatService.member_count(db, auth.tenant_id)
    return {"used": used, "included": settings.SEATS_INCLUDED_IN_BASE,
            "purchased": acc.seats_purchased,
            "capacity": SeatService.seat_capacity(acc),
            "interval": acc.billing_interval}

class SeatsReq(BaseModel):
    seats: int

@router.put("/seats")
async def set_seats(req: SeatsReq, auth=Depends(require_admin),
                    db=Depends(get_db)):
    from app.platform_services.seats import SeatService
    acc = await BillingService.get_account(db, auth.tenant_id)
    if not acc.si_seats:
        raise HTTPException(400, "no active seat subscription")
    used = await SeatService.member_count(db, auth.tenant_id)
    if settings.SEATS_INCLUDED_IN_BASE + req.seats < used:
        raise HTTPException(400, f"cannot reduce below usage ({used} members)")
    stripe.SubscriptionItem.modify(acc.si_seats, quantity=req.seats,
                                   proration_behavior="create_prorations")
    acc.seats_purchased = req.seats
    await db.commit()
    return {"ok": True, "purchased": req.seats}

class CouponReq(BaseModel):
    promo_code: str

@router.post("/coupon")
async def apply_coupon(req: CouponReq, auth=Depends(require_admin),
                       db=Depends(get_db)):
    try:
        return await BillingService.apply_coupon_to_subscription(
            db, auth.tenant_id, req.promo_code)
    except ValueError as e:
        raise HTTPException(400, str(e))

@router.get("/preview")
async def preview(auth=Depends(get_auth), db=Depends(get_db)):
    return await BillingService.preview_invoice(db, auth.tenant_id)

@router.get("/promo/{code}/validate")
async def validate_promo(code: str, auth=Depends(get_auth)):
    found = stripe.PromotionCode.list(code=code, active=True, limit=1)
    if not found.data:
        return {"valid": False}
    c = found.data[0].coupon
    return {"valid": True, "percent_off": c.get("percent_off"),
            "duration": c.get("duration")}

@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(
            payload, sig, settings.STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(400, "invalid signature")
    obj = event["data"]["object"]
    async with db_mod.session_factory() as db:
        if event["type"] in ("customer.subscription.created",
                             "customer.subscription.updated",
                             "customer.subscription.deleted"):
            await BillingService.sync_subscription(db, obj)
            await db.commit()
        elif event["type"] == "invoice.payment_failed":
            sub_id = obj.get("subscription")
            if sub_id:
                acc = (await db.execute(select(BillingAccount).where(
                    BillingAccount.base_subscription_id == sub_id
                ))).scalar_one_or_none()
                if acc:
                    await BillingService._apply_plan_quota(
                        db, acc.tenant_id, "free")
                    await db.commit()
    return {"received": True}
