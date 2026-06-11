from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.infra.db import get_db
from app.api.deps import get_auth, require_admin
from app.domain.models import Skill

router = APIRouter(prefix="/v1/skills", tags=["skills"])

class SkillReq(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""
    enabled: bool = True

@router.post("")
async def create_skill(req: SkillReq, auth=Depends(require_admin), db=Depends(get_db)):
    s = Skill(tenant_id=auth.tenant_id, **req.model_dump())
    db.add(s)
    await db.commit()
    return {"id": s.id}

@router.get("")
async def list_skills(auth=Depends(get_auth), db=Depends(get_db)):
    rows = (await db.execute(select(Skill).where(
        Skill.tenant_id == auth.tenant_id))).scalars().all()
    return [{"id": s.id, "name": s.name, "enabled": s.enabled} for s in rows]

@router.put("/{skill_id}")
async def update_skill(skill_id: str, req: SkillReq,
                       auth=Depends(require_admin), db=Depends(get_db)):
    s = await db.get(Skill, skill_id)
    if not s or s.tenant_id != auth.tenant_id:
        raise HTTPException(404)
    for k, v in req.model_dump().items():
        setattr(s, k, v)
    await db.commit()
    return {"ok": True}

@router.delete("/{skill_id}")
async def delete_skill(skill_id: str, auth=Depends(require_admin), db=Depends(get_db)):
    s = await db.get(Skill, skill_id)
    if not s or s.tenant_id != auth.tenant_id:
        raise HTTPException(404)
    await db.delete(s)
    await db.commit()
    return {"ok": True}