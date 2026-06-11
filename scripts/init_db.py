import asyncio
from sqlalchemy import text
from app.infra.db import engine, RLS_SQL
from app.domain.models import Base

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(RLS_SQL))
    print("database initialized with RLS enabled")

if __name__ == "__main__":
    asyncio.run(main())