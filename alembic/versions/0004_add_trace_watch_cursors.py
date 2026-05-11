"""add trace_watch_cursors table

Revision ID: 0004_add_trace_watch_cursors
Revises: 0003_add_system_configs
Create Date: 2026-05-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004_add_trace_watch_cursors"
down_revision = "0003_add_system_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trace_watch_cursors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_name", sa.String(256), unique=True, nullable=False),
        sa.Column("last_seen_run_id", sa.String(256), nullable=True),
        sa.Column("last_seen_run_ids", JSONB(), nullable=True),
        sa.Column("last_seen_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runs_fetched_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="active"
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_trace_watch_cursors_status", "trace_watch_cursors", ["status"])


def downgrade() -> None:
    op.drop_index("ix_trace_watch_cursors_status", table_name="trace_watch_cursors")
    op.drop_table("trace_watch_cursors")
