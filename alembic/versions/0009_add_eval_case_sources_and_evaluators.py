"""Add eval_case_sources + evaluator_configs tables (PR3a).

This DB never went through earlier alembic revisions (0008 was applied via raw
DDL). We follow the same pattern: keep this migration file as a record, but
apply the DDL through scripts/apply_pr3a_ddl.py once the file exists.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── eval_case_sources: ephemeral storage for uploaded test case files ──
    op.create_table(
        "eval_case_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),  # 'file' | 'inline'
        sa.Column("file_format", sa.String(16), nullable=True),    # 'json' | 'jsonl'
        sa.Column("cases", JSONB, nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # ── evaluator_configs: named, reusable evaluator instances ──
    op.create_table(
        "evaluator_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("evaluator_type", sa.String(32), nullable=False),  # 'exact_match' | 'tool_sequence_match' | 'llm_judge'
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # ── test_runs links ──
    op.add_column(
        "test_runs",
        sa.Column("eval_case_source_id", UUID(as_uuid=True),
                  sa.ForeignKey("eval_case_sources.id", ondelete="SET NULL"),
                  nullable=True, index=True),
    )
    op.add_column(
        "test_runs",
        sa.Column("langsmith_project", sa.Text(), nullable=True),
    )
    op.add_column(
        "test_runs",
        sa.Column("eval_started_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── test_results: keep agent question text + thread id + ls run id ──
    op.add_column(
        "test_results",
        sa.Column("question", sa.Text(), nullable=True),
    )
    op.add_column(
        "test_results",
        sa.Column("thread_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "test_results",
        sa.Column("langsmith_run_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("test_results", "langsmith_run_id")
    op.drop_column("test_results", "thread_id")
    op.drop_column("test_results", "question")
    op.drop_column("test_runs", "eval_started_at")
    op.drop_column("test_runs", "langsmith_project")
    op.drop_column("test_runs", "eval_case_source_id")
    op.drop_table("evaluator_configs")
    op.drop_table("eval_case_sources")
