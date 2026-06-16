import aioboto3
from app.config import settings

class ObjectStorage:
    def __init__(self):
        self._session = aioboto3.Session()

    def _client(self):
        return self._session.client(
            "s3", endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY)

    async def ensure_bucket(self):
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=settings.S3_BUCKET)
            except Exception:
                await s3.create_bucket(Bucket=settings.S3_BUCKET)

    @staticmethod
    def make_key(tenant_id, run_id, name):
        return f"tenants/{tenant_id}/runs/{run_id}/{name}"

    async def get(self, key: str) -> bytes:
        async with self._client() as s3:
            resp = await s3.get_object(Bucket=settings.S3_BUCKET, Key=key)
            return await resp["Body"].read()

    @staticmethod
    def checkpoint_key(tenant_id: str, run_id: str, version: int) -> str:
        return f"tenants/{tenant_id}/checkpoints/{run_id}/v{version}.json"

    async def put(self, key, data, mime="application/octet-stream"):
        async with self._client() as s3:
            await s3.put_object(Bucket=settings.S3_BUCKET, Key=key,
                                Body=data, ContentType=mime)

    async def presigned_url(self, key, expires=3600):
        async with self._client() as s3:
            return await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.S3_BUCKET, "Key": key},
                ExpiresIn=expires)

object_storage = ObjectStorage()