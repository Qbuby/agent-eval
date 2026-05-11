"""initial schema - core evaluation tables

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version_tag", sa.String(64), unique=True, nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("parent_version", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "test_cases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dataset_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("dataset_versions.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("tags", ARRAY(sa.Text), server_default="{}"),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("input_messages", JSONB, nullable=False),
        sa.Column("agent_config_override", JSONB),
        sa.Column("expected_output", sa.Text()),
        sa.Column("expected_output_criteria", JSONB, nullable=False, server_default="[]"),
        sa.Column("expected_tool_calls", JSONB, nullable=False, server_default="[]"),
        sa.Column("max_tool_calls", sa.Integer()),
        sa.Column("max_latency_ms", sa.Integer()),
        sa.Column("max_tokens", sa.Integer()),
        sa.Column("eval_weights", JSONB, nullable=False),
        sa.Column("scoring_mode", sa.String(16), nullable=False, server_default="hybrid"),
        sa.Column("parent_case_id", UUID(as_uuid=True), sa.ForeignKey("test_cases.id")),
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
    op.create_index("ix_test_cases_dataset_version_id", "test_cases", ["dataset_version_id"])

    op.create_table(
        "test_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dataset_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("dataset_versions.id"),
            nullable=False,
        ),
        sa.Column("agent_config", JSONB, nullable=False),
        sa.Column("optimization_id", UUID(as_uuid=True)),
        sa.Column("ab_group", sa.String(16)),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("summary_scores", JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "test_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id"), nullable=False
        ),
        sa.Column(
            "test_case_id", UUID(as_uuid=True), sa.ForeignKey("test_cases.id"), nullable=False
        ),
        sa.Column("actual_output", sa.Text()),
        sa.Column("actual_tool_calls", JSONB),
        sa.Column("full_trace", JSONB),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("tool_call_count", sa.Integer()),
        sa.Column("error_message", sa.Text()),
        sa.Column("error_type", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_test_results_run_id", "test_results", ["run_id"])

    op.create_table(
        "evaluation_scores",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "result_id", UUID(as_uuid=True), sa.ForeignKey("test_results.id"), nullable=False
        ),
        sa.Column("dimension", sa.String(64), nullable=False),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        sa.Column("weight", sa.Numeric(5, 4), nullable=False),
        sa.Column("weighted_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("scoring_method", sa.String(16), nullable=False),
        sa.Column("details", JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_evaluation_scores_result_id", "evaluation_scores", ["result_id"])
    op.create_index("ix_evaluation_scores_dimension", "evaluation_scores", ["dimension"])

    op.create_table(
        "optimizations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id"), nullable=False
        ),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("failure_analysis", JSONB, nullable=False),
        sa.Column("strategy_type", sa.String(32), nullable=False),
        sa.Column("strategy_detail", JSONB, nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("result_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id")),
        sa.Column("improvement_delta", JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "loop_control_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("loop_session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id")),
        sa.Column("optimization_id", UUID(as_uuid=True), sa.ForeignKey("optimizations.id")),
        sa.Column("aggregate_score", sa.Numeric(5, 4)),
        sa.Column("target_score", sa.Numeric(5, 4)),
        sa.Column("converged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("safety_stopped", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_loop_control_log_loop_session_id", "loop_control_log", ["loop_session_id"])


def downgrade() -> None:
    op.drop_index("ix_loop_control_log_loop_session_id", table_name="loop_control_log")
    op.drop_table("loop_control_log")
    op.drop_table("optimizations")
    op.drop_index("ix_evaluation_scores_dimension", table_name="evaluation_scores")
    op.drop_index("ix_evaluation_scores_result_id", table_name="evaluation_scores")
    op.drop_table("evaluation_scores")
    op.drop_index("ix_test_results_run_id", table_name="test_results")
    op.drop_table("test_results")
    op.drop_table("test_runs")
    op.drop_index("ix_test_cases_dataset_version_id", table_name="test_cases")
    op.drop_table("test_cases")
    op.drop_table("dataset_versions")
