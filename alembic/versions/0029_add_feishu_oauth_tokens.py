"""add feishu_oauth_tokens table

飞书 user OAuth（authorization code）换得的 user_access_token / refresh_token
加密存储表。防御式幂等（列/索引/表存在则跳过），照 0028 范式。

Revision ID: 0029
Revises: 0028
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "feishu_oauth_tokens" not in tables:
        op.create_table(
            "feishu_oauth_tokens",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "user_id",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("open_id", sa.String(length=128), nullable=True),
            # 仅记录归属，非 TenantMixin（不自动过滤）。无 server_default，
            # 插入由 repo 显式带 tenant_id。
            sa.Column(
                "tenant_id",
                UUID(as_uuid=True),
                sa.ForeignKey("tenants.id"),
                nullable=False,
            ),
            sa.Column("access_token_encrypted", sa.LargeBinary(), nullable=True),
            sa.Column("refresh_token_encrypted", sa.LargeBinary(), nullable=True),
            sa.Column(
                "access_token_expires_at", sa.DateTime(timezone=True), nullable=True
            ),
            sa.Column(
                "refresh_token_expires_at", sa.DateTime(timezone=True), nullable=True
            ),
            sa.Column("scope", sa.String(length=512), nullable=True),
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

    # 表可能刚建或已存在——统一按当前索引集合幂等补建。
    existing = set(insp.get_table_names())
    idx = (
        {i["name"] for i in insp.get_indexes("feishu_oauth_tokens")}
        if "feishu_oauth_tokens" in existing
        else set()
    )
    if "ix_feishu_oauth_tokens_user_id" not in idx:
        op.create_index(
            "ix_feishu_oauth_tokens_user_id",
            "feishu_oauth_tokens",
            ["user_id"],
            unique=True,
        )
    if "ix_feishu_oauth_tokens_open_id" not in idx:
        op.create_index(
            "ix_feishu_oauth_tokens_open_id",
            "feishu_oauth_tokens",
            ["open_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "feishu_oauth_tokens" in set(insp.get_table_names()):
        op.drop_table("feishu_oauth_tokens")
