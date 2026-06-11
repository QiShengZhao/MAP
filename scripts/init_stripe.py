# 幂等创建 Stripe 产品/阶梯价格/年月付/席位价/优惠券
import stripe
from app.config import settings

stripe.api_key = settings.STRIPE_API_KEY

def ensure_product(pid, name):
    try:
        return stripe.Product.retrieve(pid)
    except stripe.error.InvalidRequestError:
        return stripe.Product.create(id=pid, name=name)

def ensure_price(lookup_key, **kwargs):
    found = stripe.Price.list(lookup_keys=[lookup_key], limit=1)
    if found.data:
        return found.data[0]
    return stripe.Price.create(lookup_key=lookup_key, **kwargs)

def main():
    ensure_product("agent-pro", "Agent Platform Pro")
    ensure_product("agent-seat", "Agent Platform Seat")
    ensure_product("agent-tokens", "Agent Platform Tokens")
    ensure_product("agent-sandbox", "Agent Platform Sandbox Minutes")

    # 底价：月付 $49 / 年付 $490（约 8.3 折）
    base_month = ensure_price("pro-base-month", product="agent-pro",
        currency="usd", unit_amount=4900, recurring={"interval": "month"})
    base_year = ensure_price("pro-base-year", product="agent-pro",
        currency="usd", unit_amount=49000, recurring={"interval": "year"})

    # 席位价（licensed）：月 $15/席 / 年 $150/席
    seat_month = ensure_price("seat-month", product="agent-seat",
        currency="usd", unit_amount=1500, recurring={"interval": "month"})
    seat_year = ensure_price("seat-year", product="agent-seat",
        currency="usd", unit_amount=15000, recurring={"interval": "year"})

    # token 阶梯计价（graduated，单位=千 token）：
    # 首 1M 免费 -> 10M 内 $8/1M -> 超出 $5/1M
    tokens = ensure_price("tokens-tiered", product="agent-tokens",
        currency="usd", billing_scheme="tiered", tiers_mode="graduated",
        recurring={"interval": "month", "usage_type": "metered",
                   "aggregate_usage": "sum"},
        tiers=[{"up_to": 1000, "unit_amount_decimal": "0"},
               {"up_to": 10000, "unit_amount_decimal": "0.8"},
               {"up_to": "inf", "unit_amount_decimal": "0.5"}])

    # 沙箱阶梯：首 600 分钟免费 -> 超出 $0.01/分钟
    sandbox = ensure_price("sandbox-tiered", product="agent-sandbox",
        currency="usd", billing_scheme="tiered", tiers_mode="graduated",
        recurring={"interval": "month", "usage_type": "metered",
                   "aggregate_usage": "sum"},
        tiers=[{"up_to": 600, "unit_amount_decimal": "0"},
               {"up_to": "inf", "unit_amount_decimal": "1"}])

    # 优惠券
    for cid, kwargs in {
        "WELCOME20": dict(percent_off=20, duration="repeating",
                          duration_in_months=3, max_redemptions=1000),
        "ANNUAL50": dict(percent_off=50, duration="once"),
    }.items():
        try:
            stripe.Coupon.retrieve(cid)
        except stripe.error.InvalidRequestError:
            stripe.Coupon.create(id=cid, **kwargs)
            stripe.PromotionCode.create(coupon=cid, code=cid)

    print("STRIPE_PRICE_BASE_MONTH =", base_month.id)
    print("STRIPE_PRICE_BASE_YEAR =", base_year.id)
    print("STRIPE_PRICE_SEAT_MONTH =", seat_month.id)
    print("STRIPE_PRICE_SEAT_YEAR =", seat_year.id)
    print("STRIPE_PRICE_TOKENS_TIERED =", tokens.id)
    print("STRIPE_PRICE_SANDBOX_TIERED =", sandbox.id)

if __name__ == "__main__":
    main()