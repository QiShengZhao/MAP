from sqlalchemy import select
from app.domain.models import TenantPolicy
from app.runtime.guardrails import GuardrailBlocked

class PolicyService:
    @staticmethod
    async def get(db, tenant_id) -> TenantPolicy:
        p = (await db.execute(select(TenantPolicy).where(
            TenantPolicy.tenant_id == tenant_id))).scalar_one_or_none()
        if not p:
            p = TenantPolicy(tenant_id=tenant_id)
            db.add(p)
            await db.flush()
        return p

    @staticmethod
    def check_tool_allowed(policy, tool_name):
        allowed = policy.allowed_tools or []
        if allowed and tool_name not in allowed:
            raise GuardrailBlocked(f"tool '{tool_name}' not allowed by policy")