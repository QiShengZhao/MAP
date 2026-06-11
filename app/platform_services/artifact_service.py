from app.infra.object_storage import object_storage
from app.domain.models import Artifact

class ArtifactService:
    @staticmethod
    async def save(db, tenant_id, run_id, session_id, name, data, mime):
        key = object_storage.make_key(tenant_id, run_id, name)
        await object_storage.put(key, data, mime)
        artifact = Artifact(tenant_id=tenant_id, run_id=run_id,
                            session_id=session_id, name=name,
                            storage_key=key, mime_type=mime,
                            size_bytes=len(data))
        db.add(artifact)
        await db.flush()
        return artifact