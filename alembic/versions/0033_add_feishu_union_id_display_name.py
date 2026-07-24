"""add missing users.feishu_union_id + display_name columns

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-20

Background
----------
提交 346105c 在 ``UserRow`` 上声明了 ``feishu_union_id`` 与 ``display_name``
两列（db_models/tables.py），但**没有配套迁移**——0028 只建了 ``feishu_open_id``。
于是干净库跑到当时 head（0032）也缺这两列，登录时 ``SELECT ... feishu_union_id
... FROM users`` 直接 ``UndefinedColumnError`` → 500。本迁移补齐这两列，与 ORM
对齐。

幂等：列/索引已存在则跳过，兼容开发库里已手工存在该列的情况。

- users.feishu_union_id : 飞书 union_id（跨应用稳定唯一），unique + index，nullable
- users.display_name    : 飞书昵称，nullable，无索引
"""

from alembic import op
import sqlalchemy as sa


revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def _cols(insp, table: str) -> set:
    return {c["name"] for c in insp.get_columns(table)}


def _idx(insp, table: str) -> set:
    return {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "users" not in insp.get_table_names():
        return

    cols = _cols(insp, "users")
    if "feishu_union_id" not in cols:
        op.add_column(
            "users",
            sa.Column("feishu_union_id", sa.String(length=128), nullable=True),
        )
    if "display_name" not in cols:
        op.add_column(
            "users",
            sa.Column("display_name", sa.String(length=128), nullable=True),
        )

    if "ix_users_feishu_union_id" not in _idx(insp, "users"):
        op.create_index(
            "ix_users_feishu_union_id",
            "users",
            ["feishu_union_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "users" not in insp.get_table_names():
        return

    if "ix_users_feishu_union_id" in _idx(insp, "users"):
        op.drop_index("ix_users_feishu_union_id", table_name="users")

    cols = _cols(insp, "users")
    if "display_name" in cols:
        op.drop_column("users", "display_name")
    if "feishu_union_id" in cols:
        op.drop_column("users", "feishu_union_id")
