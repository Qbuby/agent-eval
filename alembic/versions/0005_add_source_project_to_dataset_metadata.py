"""create dataset_metadata table (with source_project)

Revision ID: 0005_add_source_project
Revises: 0004_add_trace_watch_cursors
Create Date: 2026-05-06

注：原版本只 add_column 了 source_project，假设 dataset_metadata 表已存在；
但前置迁移从未创建该表，导致全新部署时 ALTER TABLE 报 UndefinedTable。
本次修正改为整表创建（schema 与 ORM `DatasetMetadataRow` 一致）。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005_add_source_project"
down_revision = "0004_add_trace_watch_cursors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_metadata",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_name", sa.String(256), nullable=False, index=True),
        sa.Column("source_project", sa.String(256), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("max_examples", sa.Integer, nullable=True),
        sa.Column("retention_policy", sa.String(16), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("dataset_name", name="uq_dataset_metadata_name"),
    )


def downgrade() -> None:
    op.drop_table("dataset_metadata")
