"""agent memory tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

from app.infra.db import RLS_SQL

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())

    # 0001 uses Base.metadata.create_all(), so fresh installs already have these tables.
    if "session_summaries" not in tables:
        op.create_table(
            "session_summaries",
            sa.Column("session_id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("last_message_id", sa.String(length=36), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_session_summaries_tenant_id", "session_summaries", ["tenant_id"])

    if "memory_items" not in tables:
        op.create_table(
            "memory_items",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=True),
            sa.Column("session_id", sa.String(length=36), nullable=True),
            sa.Column("run_id", sa.String(length=36), nullable=True),
            sa.Column("user_id", sa.String(length=36), nullable=True),
            sa.Column("scope", sa.String(length=16), nullable=False, server_default="workspace"),
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="note"),
            sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("source_type", sa.String(length=32), nullable=False, server_default="system"),
            sa.Column("source_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0.6"),
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("embedding", sa.JSON(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        for column in ("tenant_id", "workspace_id", "session_id", "run_id", "user_id"):
            op.create_index(f"ix_memory_items_{column}", "memory_items", [column])
        op.create_index(
            "ix_memory_scope",
            "memory_items",
            ["tenant_id", "scope", "workspace_id", "user_id"],
        )

    if bind.dialect.name == "postgresql":
        op.execute(text(RLS_SQL))


def downgrade() -> None:
    op.drop_index("ix_memory_scope", table_name="memory_items")
    for column in ("user_id", "run_id", "session_id", "workspace_id", "tenant_id"):
        op.drop_index(f"ix_memory_items_{column}", table_name="memory_items")
    op.drop_table("memory_items")
    op.drop_index("ix_session_summaries_tenant_id", table_name="session_summaries")
    op.drop_table("session_summaries")
