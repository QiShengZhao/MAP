"""force tenant RLS policies

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-14
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from app.infra.db import RLS_SQL

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(text(RLS_SQL))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(text("""
    DO $$
    DECLARE t RECORD;
    BEGIN
      FOR t IN
        SELECT table_name FROM information_schema.columns
        WHERE column_name = 'tenant_id' AND table_schema = 'public'
      LOOP
        EXECUTE format('ALTER TABLE %I NO FORCE ROW LEVEL SECURITY', t.table_name);
      END LOOP;
    END $$;
    """))
