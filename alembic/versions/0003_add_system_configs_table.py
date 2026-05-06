"""add system_configs table

Revision ID: 0003_add_system_configs
Revises: 0002_add_auth_tables
Create Date: 2026-05-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0003_add_system_configs"
down_revision = "0002_add_auth_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(256), unique=True, nullable=False, index=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("category", sa.String(32), nullable=False, server_default="general"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("system_configs")
