import logging
from datetime import datetime
import stripe
from sqlalchemy import select
from app.config import settings
from app.domain.models import BillingAccount, Tenant, TenantPolicy

log = logging.getLogger("billing")
stripe.api_key = settings.STRIPE_API_KEY

PLAN_QUOTAS = {
    "free": {"max_concurrent_runs": 2, "max_tokens_per_day": 200_000},
    "pro": {"max_concurrent_runs": 10, "max_tokens_per_day": 5_000_000},
    "enterprise": {"max_concurrent_runs": 50, "max_tokens_per_day": 100_000_000},
}

class BillingService:
    @staticmethod
    async def get_account(db, tenant_id) -> BillingAccount:
        acc = await db.get(BillingAccount, tenant_id)
        if not acc:
            acc = BillingAccount(tenant_id=tenant_id)
            db.add(acc)
            await db.flush()
        return acc

    @staticmethod
    async def ensure_customer(db, tenant_id, email) -> BillingAccount:
        acc = await BillingService.get_account(db, tenant_id)
        if not acc.stripe_customer_id:
            tenant = await db.get(Tenant, tenant_id)
            customer = stripe.Customer.create(
                email=email, name=tenant.name,
                metadata={"tenant_id": tenant_id})
            acc.stripe_customer_id = customer.id
            await db.flush()
        return acc

    @staticmethod
    def _base_prices(interval):
        if interval == "year":
            return (settings.STRIPE_PRICE_BASE_YEAR,
                    settings.STRIPE_PRICE_SEAT_YEAR)
        return (settings.STRIPE_PRICE_BASE_MONTH,
                settings.STRIPE_PRICE_SEAT_MONTH)

    @staticmethod
    async def create_checkout(db, tenant_id, email, promo_code=None,
                              currency=None, interval="month", seats=0):
        """订阅A：底价+席位（月/年付）；订阅B（metered）由 webhook 自动创建"""
        if interval not in ("month", "year"):
            raise ValueError("interval must be month|year")
        acc = await BillingService.ensure_customer(db, tenant_id, email)
        base_price, seat_price = BillingService._base_prices(interval)
        line_items = [{"price": base_price, "quantity": 1}]
        if seats > 0:
            line_items.append({"price": seat_price, "quantity": seats})
        params = dict(
            customer=acc.stripe_customer_id, mode="subscription",
            line_items=line_items,
            subscription_data={
                "metadata": {"tenant_id": tenant_id, "sub_role": "base",
                             "interval": interval},
                "trial_period_days": settings.BILLING_TRIAL_DAYS or None},
            allow_promotion_codes=True,
            success_url=f"{settings.BILLING_PUBLIC_URL}/billing/success",
            cancel_url=f"{settings.BILLING_PUBLIC_URL}/billing/cancel")
        if settings.STRIPE_AUTOMATIC_TAX:
            params["automatic_tax"] = {"enabled": True}
            params["customer_update"] = {"address": "auto", "name": "auto"}
            params["tax_id_collection"] = {"enabled": True}
        if currency:
            params["currency"] = currency
        if promo_code:
            codes = stripe.PromotionCode.list(code=promo_code, active=True,
                                              limit=1)
            if not codes.data:
                raise ValueError(f"invalid promo code: {promo_code}")
            params.pop("allow_promotion_codes")
            params["discounts"] = [{"promotion_code": codes.data[0].id}]
        return stripe.checkout.Session.create(**params).url

    @staticmethod
    async def create_portal(db, tenant_id):
        acc = await BillingService.get_account(db, tenant_id)
        portal = stripe.billing_portal.Session.create(
            customer=acc.stripe_customer_id,
            return_url=f"{settings.BILLING_PUBLIC_URL}/")
        return portal.url

    @staticmethod
    async def ensure_usage_subscription(db, acc):
        """base 激活后幂等创建月付 metered 用量订阅"""
        if acc.usage_subscription_id:
            return
        sub = stripe.Subscription.create(
            customer=acc.stripe_customer_id,
            items=[{"price": settings.STRIPE_PRICE_TOKENS_TIERED},
                   {"price": settings.STRIPE_PRICE_SANDBOX_TIERED}],
            metadata={"tenant_id": acc.tenant_id, "sub_role": "usage"})
        acc.usage_subscription_id = sub.id
        for item in sub["items"]["data"]:
            pid = item["price"]["id"]
            if pid == settings.STRIPE_PRICE_TOKENS_TIERED:
                acc.si_tokens = item["id"]
            elif pid == settings.STRIPE_PRICE_SANDBOX_TIERED:
                acc.si_sandbox = item["id"]
        await db.flush()

    @staticmethod
    async def switch_interval(db, tenant_id, interval):
        acc = await BillingService.get_account(db, tenant_id)
        if not acc.base_subscription_id:
            raise ValueError("no base subscription")
        if interval == acc.billing_interval:
            return {"changed": False}
        base_price, seat_price = BillingService._base_prices(interval)
        sub = stripe.Subscription.retrieve(acc.base_subscription_id)
        items = []
        for item in sub["items"]["data"]:
            lookup = item["price"].get("lookup_key") or ""
            if lookup.startswith("pro-base"):
                items.append({"id": item["id"], "price": base_price})
            elif lookup.startswith("seat"):
                items.append({"id": item["id"], "price": seat_price,
                              "quantity": item["quantity"]})
        stripe.Subscription.modify(acc.base_subscription_id, items=items,
                                   proration_behavior="create_prorations")
        acc.billing_interval = interval
        await db.flush()
        return {"changed": True, "interval": interval}

    @staticmethod
    async def apply_coupon_to_subscription(db, tenant_id, promo_code):
        acc = await BillingService.get_account(db, tenant_id)
        if not acc.base_subscription_id:
            raise ValueError("no active subscription")
        codes = stripe.PromotionCode.list(code=promo_code, active=True, limit=1)
        if not codes.data:
            raise ValueError(f"invalid promo code: {promo_code}")
        stripe.Subscription.modify(
            acc.base_subscription_id,
            discounts=[{"promotion_code": codes.data[0].id}])
        return {"applied": True}

    @staticmethod
    async def preview_invoice(db, tenant_id):
        acc = await BillingService.get_account(db, tenant_id)
        if not acc.base_subscription_id:
            return {"plan": "free", "amount_due": 0}
        inv = stripe.Invoice.upcoming(
            customer=acc.stripe_customer_id,
            subscription=acc.base_subscription_id)
        return {"plan": acc.plan, "currency": inv["currency"],
                "subtotal": inv["subtotal"] / 100,
                "tax": (inv.get("tax") or 0) / 100,
                "amount_due": inv["amount_due"] / 100,
                "lines": [{"description": l.get("description"),
                           "quantity": l.get("quantity"),
                           "amount": l["amount"] / 100}
                          for l in inv["lines"]["data"]]}

    @staticmethod
    async def sync_subscription(db, sub):
        tenant_id = (sub.get("metadata") or {}).get("tenant_id")
        role = (sub.get("metadata") or {}).get("sub_role", "base")
        if not tenant_id:
            return
        acc = await BillingService.get_account(db, tenant_id)
        if role == "base":
            acc.base_subscription_id = sub["id"]
            acc.status = sub["status"]
            acc.billing_interval = (sub.get("metadata") or {}).get(
                "interval", acc.billing_interval)
            acc.current_period_end = datetime.utcfromtimestamp(
                sub["current_period_end"])
            for item in sub["items"]["data"]:
                lookup = item["price"].get("lookup_key") or ""
                if lookup.startswith("seat"):
                    acc.si_seats = item["id"]
                    acc.seats_purchased = item["quantity"]
                elif lookup.startswith("pro-base"):
                    acc.plan = "pro"
            if sub["status"] in ("active", "trialing"):
                await BillingService.ensure_usage_subscription(db, acc)
            else:
                acc.plan = "free"
            await BillingService._apply_plan_quota(db, tenant_id, acc.plan)
        elif role == "usage":
            acc.usage_subscription_id = sub["id"]
            for item in sub["items"]["data"]:
                pid = item["price"]["id"]
                if pid == settings.STRIPE_PRICE_TOKENS_TIERED:
                    acc.si_tokens = item["id"]
                elif pid == settings.STRIPE_PRICE_SANDBOX_TIERED:
                    acc.si_sandbox = item["id"]
        await db.flush()

    @staticmethod
    async def _apply_plan_quota(db, tenant_id, plan):
        quota = PLAN_QUOTAS.get(plan, PLAN_QUOTAS["free"])
        policy = (await db.execute(select(TenantPolicy).where(
            TenantPolicy.tenant_id == tenant_id))).scalar_one_or_none()
        if policy:
            policy.max_concurrent_runs = quota["max_concurrent_runs"]
            policy.max_tokens_per_day = quota["max_tokens_per_day"]
        tenant = await db.get(Tenant, tenant_id)
        if tenant:
            tenant.plan = plan