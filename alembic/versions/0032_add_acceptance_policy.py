"""add immutable acceptance policy snapshot to test runs

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    return any(item["name"] == column for item in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "test_runs" not in insp.get_table_names():
        return
    if not _has_column(insp, "test_runs", "acceptance_policy"):
        op.add_column(
            "test_runs",
            sa.Column("acceptance_policy", JSONB(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "test_runs" not in insp.get_table_names():
        return
    if _has_column(insp, "test_runs", "acceptance_policy"):
        op.drop_column("test_runs", "acceptance_policy")
