"""add Langfuse metrics tables (trace / observation / cursor)

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-11

Background
----------
Langfuse 指标周期拉取的持久化层（§ Langfuse 指标）。三张表均挂 TenantMixin：
后台轮询无租户上下文 → 写入自动落 INTERNAL_TENANT_ID sentinel，superadmin 读取
不被过滤、跨 env 全见。

- ``langfuse_trace_metrics``     trace 级聚合指标，按 ``langfuse_trace_id`` 幂等 upsert
- ``langfuse_observation_metrics`` observation 明细，业务键 ``langfuse_observation_id`` 唯一
- ``langfuse_metrics_cursors``   单例游标 / 运行状态（scope="global" 固定一行）

字段逐列对照 db_models/tables.py 中三个权威模型。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_SENTINEL = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")


def upgrade() -> None:
    # ── 1. langfuse_trace_metrics ────────────────────────────────────────
    op.create_table(
        "langfuse_trace_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=_SENTINEL,
        ),
        sa.Column("langfuse_trace_id", sa.String(256), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("name", sa.String(512), nullable=True),
        sa.Column("trace_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.String(256), nullable=True),
        sa.Column("user_id", sa.String(256), nullable=True),
        sa.Column("release", sa.String(128), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column("latency_s", sa.Numeric(12, 4), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("total_cost", sa.Numeric(12, 6), nullable=True),
        sa.Column("first_tool_call_s", sa.Numeric(12, 4), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tool_success_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tool_error_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tool_success_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("tool_call_counts", postgresql.JSONB(), nullable=True),
        sa.Column("first_thinking_token_s", sa.Numeric(12, 4), nullable=True),
        sa.Column("first_answer_token_s", sa.Numeric(12, 4), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_hit_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("generation_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("has_error", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("input", postgresql.JSONB(), nullable=True),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column("trace_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("scores", postgresql.JSONB(), nullable=True),
        sa.Column(
            "raw_synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="fk_langfuse_trace_metrics_tenant_id_tenants",
        ),
    )
    op.create_index(
        "ix_langfuse_trace_metrics_langfuse_trace_id",
        "langfuse_trace_metrics", ["langfuse_trace_id"], unique=True,
    )
    op.create_index(
        "ix_langfuse_trace_metrics_environment",
        "langfuse_trace_metrics", ["environment"],
    )
    op.create_index(
        "ix_langfuse_trace_metrics_trace_timestamp",
        "langfuse_trace_metrics", ["trace_timestamp"],
    )
    op.create_index(
        "ix_langfuse_trace_metrics_tenant_id",
        "langfuse_trace_metrics", ["tenant_id"],
    )
    op.create_index(
        "ix_lf_trace_env_ts",
        "langfuse_trace_metrics", ["environment", "trace_timestamp"],
    )

    # ── 2. langfuse_observation_metrics ──────────────────────────────────
    op.create_table(
        "langfuse_observation_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=_SENTINEL,
        ),
        sa.Column("langfuse_observation_id", sa.String(256), nullable=False),
        sa.Column("langfuse_trace_id", sa.String(256), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("trace_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(512), nullable=True),
        sa.Column("level", sa.String(16), nullable=True),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_s", sa.Numeric(12, 4), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("usage_input", sa.Integer(), nullable=True),
        sa.Column("usage_output", sa.Integer(), nullable=True),
        sa.Column("usage_total", sa.Integer(), nullable=True),
        sa.Column("calculated_total_cost", sa.Numeric(12, 6), nullable=True),
        sa.Column("total_price", sa.Numeric(12, 6), nullable=True),
        sa.Column("time_to_first_token_s", sa.Numeric(12, 4), nullable=True),
        sa.Column("completion_start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parent_observation_id", sa.String(256), nullable=True),
        sa.Column("obs_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("input", postgresql.JSONB(), nullable=True),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="fk_langfuse_observation_metrics_tenant_id_tenants",
        ),
        sa.UniqueConstraint("langfuse_observation_id", name="uq_lf_obs_observation_id"),
    )
    op.create_index(
        "ix_langfuse_observation_metrics_langfuse_observation_id",
        "langfuse_observation_metrics", ["langfuse_observation_id"],
    )
    op.create_index(
        "ix_langfuse_observation_metrics_langfuse_trace_id",
        "langfuse_observation_metrics", ["langfuse_trace_id"],
    )
    op.create_index(
        "ix_langfuse_observation_metrics_environment",
        "langfuse_observation_metrics", ["environment"],
    )
    op.create_index(
        "ix_langfuse_observation_metrics_type",
        "langfuse_observation_metrics", ["type"],
    )
    op.create_index(
        "ix_langfuse_observation_metrics_start_time",
        "langfuse_observation_metrics", ["start_time"],
    )
    op.create_index(
        "ix_langfuse_observation_metrics_trace_timestamp",
        "langfuse_observation_metrics", ["trace_timestamp"],
    )
    op.create_index(
        "ix_langfuse_observation_metrics_tenant_id",
        "langfuse_observation_metrics", ["tenant_id"],
    )
    op.create_index(
        "ix_lf_obs_trace_type",
        "langfuse_observation_metrics", ["langfuse_trace_id", "type"],
    )

    # ── 3. langfuse_metrics_cursors ──────────────────────────────────────
    op.create_table(
        "langfuse_metrics_cursors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=_SENTINEL,
        ),
        sa.Column("scope", sa.String(64), nullable=False, server_default=sa.text("'global'")),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("traces_synced_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("observations_synced_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_run_traces", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_run_observations", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'idle'")),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="fk_langfuse_metrics_cursors_tenant_id_tenants",
        ),
        sa.UniqueConstraint("scope", name="uq_lf_metrics_cursor_scope"),
    )
    op.create_index(
        "ix_langfuse_metrics_cursors_tenant_id",
        "langfuse_metrics_cursors", ["tenant_id"],
    )

    # Seed 单例游标行（scope=global, status=idle）。
    op.execute(
        "INSERT INTO langfuse_metrics_cursors (id, tenant_id, scope, status) "
        "VALUES (gen_random_uuid(), '00000000-0000-0000-0000-000000000001', 'global', 'idle')"
    )


def downgrade() -> None:
    # 逆序：cursors → observation → trace
    op.drop_index("ix_langfuse_metrics_cursors_tenant_id", table_name="langfuse_metrics_cursors")
    op.drop_table("langfuse_metrics_cursors")

    op.drop_index("ix_lf_obs_trace_type", table_name="langfuse_observation_metrics")
    op.drop_index("ix_langfuse_observation_metrics_tenant_id", table_name="langfuse_observation_metrics")
    op.drop_index("ix_langfuse_observation_metrics_trace_timestamp", table_name="langfuse_observation_metrics")
    op.drop_index("ix_langfuse_observation_metrics_start_time", table_name="langfuse_observation_metrics")
    op.drop_index("ix_langfuse_observation_metrics_type", table_name="langfuse_observation_metrics")
    op.drop_index("ix_langfuse_observation_metrics_environment", table_name="langfuse_observation_metrics")
    op.drop_index("ix_langfuse_observation_metrics_langfuse_trace_id", table_name="langfuse_observation_metrics")
    op.drop_index("ix_langfuse_observation_metrics_langfuse_observation_id", table_name="langfuse_observation_metrics")
    op.drop_table("langfuse_observation_metrics")

    op.drop_index("ix_lf_trace_env_ts", table_name="langfuse_trace_metrics")
    op.drop_index("ix_langfuse_trace_metrics_tenant_id", table_name="langfuse_trace_metrics")
    op.drop_index("ix_langfuse_trace_metrics_trace_timestamp", table_name="langfuse_trace_metrics")
    op.drop_index("ix_langfuse_trace_metrics_environment", table_name="langfuse_trace_metrics")
    op.drop_index("ix_langfuse_trace_metrics_langfuse_trace_id", table_name="langfuse_trace_metrics")
    op.drop_table("langfuse_trace_metrics")
