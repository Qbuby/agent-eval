"""add dual-model comparative evaluation columns

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-17

Additive & nullable only — single-mode runs are unaffected:
- test_runs.eval_mode       : 'single' (default) | 'comparative'
- test_runs.agent_config_b  : B agent config snapshot (NULL for single)
- test_results.comparison   : per-case comparison verdict (NULL for single)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    return any(item["name"] == column for item in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()

    if "test_runs" in tables:
        if not _has_column(insp, "test_runs", "eval_mode"):
            op.add_column(
                "test_runs",
                sa.Column(
                    "eval_mode",
                    sa.String(length=16),
                    nullable=False,
                    server_default="single",
                ),
            )
        if not _has_column(insp, "test_runs", "agent_config_b"):
            op.add_column(
                "test_runs",
                sa.Column("agent_config_b", JSONB(), nullable=True),
            )

    if "test_results" in tables:
        if not _has_column(insp, "test_results", "comparison"):
            op.add_column(
                "test_results",
                sa.Column("comparison", JSONB(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()

    if "test_results" in tables and _has_column(insp, "test_results", "comparison"):
        op.drop_column("test_results", "comparison")

    if "test_runs" in tables:
        if _has_column(insp, "test_runs", "agent_config_b"):
            op.drop_column("test_runs", "agent_config_b")
        if _has_column(insp, "test_runs", "eval_mode"):
            op.drop_column("test_runs", "eval_mode")
