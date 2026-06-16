from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.infra.db import get_db
from app.api.deps import get_auth, require_admin
from app.domain.models import Skill
from app.skills.catalog import CATALOG, get_catalog_skill
from app.skills.importer import SkillImportError, fetch_skill

router = APIRouter(prefix="/v1/skills", tags=["skills"])

class SkillReq(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""
    enabled: bool = True


class SkillImportReq(BaseModel):
    catalog_id: str | None = None
    url: str | None = None


def _import_url(req: SkillImportReq) -> str:
    if bool(req.catalog_id) == bool(req.url):
        raise HTTPException(422, "catalog_id 与 url 必须且只能提供一个")
    if req.catalog_id:
        catalog_skill = get_catalog_skill(req.catalog_id)
        if not catalog_skill:
            raise HTTPException(404, "catalog skill not found")
        return catalog_skill.source_url
    return req.url or ""


async def _load_import(req: SkillImportReq):
    try:
        return await fetch_skill(_import_url(req))
    except SkillImportError as exc:
        raise HTTPException(400, str(exc)) from exc

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
    return [{
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "instructions": s.instructions,
        "enabled": s.enabled,
    } for s in rows]


@router.get("/catalog")
async def list_catalog(auth=Depends(get_auth), db=Depends(get_db)):
    names = set((await db.execute(select(Skill.name).where(
        Skill.tenant_id == auth.tenant_id))).scalars().all())
    return [
        {**skill.as_dict(), "installed": skill.name.lower() in {
            name.lower() for name in names
        }}
        for skill in CATALOG
    ]


@router.post("/import/preview")
async def preview_skill(req: SkillImportReq, auth=Depends(require_admin)):
    imported = await _load_import(req)
    return imported.as_dict()


@router.post("/import", status_code=201)
async def import_skill(req: SkillImportReq, auth=Depends(require_admin),
                       db=Depends(get_db)):
    imported = await _load_import(req)
    duplicate = await db.scalar(select(Skill).where(
        Skill.tenant_id == auth.tenant_id,
        Skill.name == imported.name,
    ))
    if duplicate:
        raise HTTPException(409, f"Skill「{imported.name}」已安装")
    skill = Skill(
        tenant_id=auth.tenant_id,
        name=imported.name,
        description=imported.description,
        instructions=imported.instructions,
        enabled=True,
    )
    db.add(skill)
    await db.commit()
    return {"id": skill.id, **imported.as_dict()}

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
