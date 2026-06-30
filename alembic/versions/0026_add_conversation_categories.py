"""add conversation_categories table (受管单值类别 for 多轮对话集)

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-25

Background
----------
多轮对话集对齐基准测试集的「受管类别」：列出 / 新建 / 重命名 / 删除（带引用保护）
/ 按类别过滤。基准用 ``categories`` 表挂 ``project_id``，但多轮对话样例存在
Langfuse（Postgres 没有对话 case 表），没有 project 概念——其边界是 dataset。
故新建独立表 ``conversation_categories``，作用域换成 ``dataset_name``（与
``dataset_metadata`` / ``candidate_cases`` 一致的 dataset 键）。

case→类别的关联不走外键（case 在 Langfuse），而是把类别名字符串写进
Langfuse item 的 ``metadata["category"]``（单值）。本表只承担「受管实体」职责：
保证类别可重命名 / 删除 / 列举 / dataset 内唯一。

挂 TenantMixin（tenant_id NOT NULL, default 内部 sentinel），与 dataset_metadata
对齐。幂等：表已存在则跳过。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_INTERNAL_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "conversation_categories" in insp.get_table_names():
        return

    op.create_table(
        "conversation_categories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_name", sa.String(256), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
            server_default=_INTERNAL_TENANT_ID,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "dataset_name", "name", name="uq_conv_category_dataset_name"
        ),
    )
    # dataset_name 的索引已由 create_table 的 Column(index=True) 自动建出，
    # 这里只补 tenant_id 索引（Column 未声明 index）。
    op.create_index(
        "ix_conversation_categories_tenant_id",
        "conversation_categories",
        ["tenant_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "conversation_categories" not in insp.get_table_names():
        return
    # drop_table 会一并移除其索引/约束，无需逐个 drop_index。
    op.drop_table("conversation_categories")
