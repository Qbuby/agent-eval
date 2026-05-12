"""add source_project to dataset_metadata

Revision ID: 0005_add_source_project
Revises: 0004_add_trace_watch_cursors
Create Date: 2026-05-06
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_add_source_project"
down_revision = "0004_add_trace_watch_cursors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dataset_metadata",
        sa.Column("source_project", sa.String(256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dataset_metadata", "source_project")
