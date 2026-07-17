"""add feishu_conversation_messages table (飞书机器人多轮对话历史)

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-07

Background
----------
飞书机器人此前无状态：每条消息独立进 orchestration，无法引用上文。这张表按
``(tenant_id, open_id)`` 归档每轮的 user / assistant 文本，取数时按时间正序取
最近 N 条注入 LLM 的 messages，实现多轮记忆。

只存文本（图片以 base64 一次性传入编排，不回灌历史，避免撑爆上下文与 DB）。
挂 TenantMixin（tenant_id NOT NULL, default 内部 sentinel），与其它客户可分离
表对齐——历史归属发消息的租户，读写在该租户上下文里过滤/盖章。
幂等：表已存在则跳过。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_INTERNAL_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "feishu_conversation_messages" in insp.get_table_names():
        return

    op.create_table(
        "feishu_conversation_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("open_id", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),  # user | assistant
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
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
        "ix_feishu_conversation_messages_tenant_id",
        "feishu_conversation_messages",
        ["tenant_id"],
    )
    op.create_index(
        "ix_feishu_conversation_messages_open_id",
        "feishu_conversation_messages",
        ["open_id"],
    )
    # 取数按 (tenant_id, open_id) 过滤 + created_at 正序，建复合索引。
    op.create_index(
        "ix_feishu_conv_tenant_open_created",
        "feishu_conversation_messages",
        ["tenant_id", "open_id", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "feishu_conversation_messages" not in insp.get_table_names():
        return
    # drop_table 会一并移除其索引，无需逐个 drop_index。
    op.drop_table("feishu_conversation_messages")
