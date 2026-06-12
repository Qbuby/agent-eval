"""add entry_codes table + seed internal entry code

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-11

Background
----------
注册入口码（§ 入口码功能）。用户注册时凭码绑定到某租户并获得指定角色：
- 内部码 ``AiDong2026!`` -> 内部租户(sentinel) + role=user
- 客户租户码（如中力 ``Ep2026!``）由 admin 在 UI 里建租户后再建码，不在迁移里焊死

entry_codes 表**不挂 tenant_id 过滤**（与 tenants 一样是全局维度表）：注册在
pre-auth 阶段查码，没有租户上下文；且内部 admin 要跨租户管理所有码。code 明文存储
（admin 需查看并分发给客户）。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_INTERNAL_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.create_table(
        "entry_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
            server_default=_INTERNAL_TENANT_ID,
        ),
        sa.Column("role", sa.String(32), nullable=False, server_default="user"),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
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
    op.create_index("ix_entry_codes_code", "entry_codes", ["code"], unique=True)

    # Seed 内部入口码：指向内部租户 sentinel，角色 user。
    op.execute(
        sa.text(
            """
            INSERT INTO entry_codes (id, code, tenant_id, role, label, is_active,
                                     created_by, created_at, updated_at)
            VALUES (gen_random_uuid(), 'AiDong2026!', :tid, 'user', '内部入口', true,
                    NULL, now(), now())
            """
        ).bindparams(tid=_INTERNAL_TENANT_ID)
    )


def downgrade() -> None:
    op.drop_index("ix_entry_codes_code", table_name="entry_codes")
    op.drop_table("entry_codes")
