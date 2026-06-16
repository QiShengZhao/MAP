"""platform admin flag on users

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "is_platform_admin" not in {c["name"] for c in inspect(bind).get_columns("users")}:
        op.add_column(
            "users",
            sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_column("users", "is_platform_admin")
