from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from app.infra.db import get_db
from app.api.deps import get_auth, require_admin
from app.domain.models import AgentDef

router = APIRouter(prefix="/v1/agents", tags=["agents"])

class AgentReq(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""
    model: str = "gpt-4o"
    tools: list[str] = []
    handoffs: list[str] = []
    as_tool: bool = False
    is_default: bool = False
    enabled: bool = True

@router.post("")
async def create_agent(req: AgentReq, auth=Depends(require_admin), db=Depends(get_db)):
    if req.is_default:
        await db.execute(update(AgentDef).where(
            AgentDef.tenant_id == auth.tenant_id).values(is_default=False))
    a = AgentDef(tenant_id=auth.tenant_id, **req.model_dump())
    db.add(a)
    await db.commit()
    return {"id": a.id}

@router.get("")
async def list_agents(auth=Depends(get_auth), db=Depends(get_db)):
    rows = (await db.execute(select(AgentDef).where(
        AgentDef.tenant_id == auth.tenant_id))).scalars().all()
    return [{"id": a.id, "name": a.name, "model": a.model,
             "handoffs": a.handoffs, "as_tool": a.as_tool,
             "is_default": a.is_default, "enabled": a.enabled} for a in rows]

@router.put("/{agent_id}")
async def update_agent(agent_id: str, req: AgentReq,
                       auth=Depends(require_admin), db=Depends(get_db)):
    a = await db.get(AgentDef, agent_id)
    if not a or a.tenant_id != auth.tenant_id:
        raise HTTPException(404)
    if req.is_default:
        await db.execute(update(AgentDef).where(
            AgentDef.tenant_id == auth.tenant_id).values(is_default=False))
    for k, v in req.model_dump().items():
        setattr(a, k, v)
    await db.commit()
    return {"ok": True}