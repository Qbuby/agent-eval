"""add scheduled_eval_tasks table (定时评估任务)

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-07

Background
----------
定时评估：按 interval / 每日定点自动发起评估 run。持久化一份等价
``StartEvalRequest`` 的 spec(JSONB) + schedule(JSONB) + 通知目标 + 调度游标
（next_run_at / last_run_at / last_run_id）。调度器（EvalScheduler）单循环扫
``enabled and next_run_at <= now`` 的任务，用同一条 ``resolve_eval_start_args``
链路复跑，避免与 HTTP /runs/start 逻辑分叉。

挂 TenantMixin（tenant_id NOT NULL, default 内部 sentinel），与其它客户可分离
表对齐——定时任务归属创建它的租户，调度器在该租户上下文里发起 run。
幂等：表已存在则跳过。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_INTERNAL_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "scheduled_eval_tasks" in insp.get_table_names():
        return

    op.create_table(
        "scheduled_eval_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("spec", JSONB, nullable=False),
        sa.Column("schedule", JSONB, nullable=False),
        sa.Column(
            "notify_open_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
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
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
            server_default=_INTERNAL_TENANT_ID,
        ),
    )
    op.create_index(
        "ix_scheduled_eval_tasks_tenant_id", "scheduled_eval_tasks", ["tenant_id"]
    )
    # 调度器按 next_run_at 扫 due 任务，建索引。
    op.create_index(
        "ix_scheduled_eval_tasks_next_run_at", "scheduled_eval_tasks", ["next_run_at"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "scheduled_eval_tasks" not in insp.get_table_names():
        return
    # drop_table 会一并移除其索引/约束，无需逐个 drop_index。
    op.drop_table("scheduled_eval_tasks")
