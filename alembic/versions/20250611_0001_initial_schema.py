"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-11

Creates all tables from SQLAlchemy models and enables PostgreSQL RLS policies.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from app.infra.db import RLS_SQL

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PG_ENUMS = ("runstatus", "approvalstatus")


def upgrade() -> None:
    from app.domain.models import Base

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name == "postgresql":
        op.execute(text(RLS_SQL))


def downgrade() -> None:
    from app.domain.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
    if bind.dialect.name == "postgresql":
        for enum_name in _PG_ENUMS:
            op.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
