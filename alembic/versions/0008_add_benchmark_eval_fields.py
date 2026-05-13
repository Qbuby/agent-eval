"""Add benchmark eval fields to test_runs/test_results

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # test_runs: allow benchmark-driven runs (no dataset_version), and remember
    # which benchmark version + langfuse run + evaluator setup was used.
    op.alter_column(
        "test_runs", "dataset_version_id",
        existing_type=UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column(
        "test_runs",
        sa.Column(
            "benchmark_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("benchmark_versions.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    op.add_column(
        "test_runs",
        sa.Column("langfuse_run_name", sa.Text(), nullable=True),
    )
    op.add_column(
        "test_runs",
        sa.Column(
            "evaluator_configs",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # test_results: same — link to benchmark_cases when there's no test_case row,
    # and stash the langfuse trace id for deep-linking from the UI.
    op.alter_column(
        "test_results", "test_case_id",
        existing_type=UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column(
        "test_results",
        sa.Column(
            "benchmark_case_id",
            UUID(as_uuid=True),
            sa.ForeignKey("benchmark_cases.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    op.add_column(
        "test_results",
        sa.Column("langfuse_trace_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("test_results", "langfuse_trace_id")
    op.drop_column("test_results", "benchmark_case_id")
    op.alter_column(
        "test_results", "test_case_id",
        existing_type=UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_column("test_runs", "evaluator_configs")
    op.drop_column("test_runs", "langfuse_run_name")
    op.drop_column("test_runs", "benchmark_version_id")
    op.alter_column(
        "test_runs", "dataset_version_id",
        existing_type=UUID(as_uuid=True),
        nullable=False,
    )
