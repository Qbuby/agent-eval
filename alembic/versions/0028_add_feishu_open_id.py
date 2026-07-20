"""add feishu_open_id column to users

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-03

Background
----------
飞书机器人集成：把飞书用户（open_id）映射到 agent-eval 的 UserRow，之后机器人
以该 user 的 role/tenant 代表其执行权限内操作。open_id 是飞书应用维度下用户的
稳定唯一标识，故建唯一索引（nullable：未绑定飞书的存量用户该列为 NULL）。

绑定流程走入口码（entry_code）范式：飞书用户首次对话发入口码 → 校验 → 写
open_id↔user。无绑定元数据表，先用这一列承载映射；复杂化再拆关联表。

逐列对照 db_models/tables.py 的 UserRow.feishu_open_id。
"""

from alembic import op
import sqlalchemy as sa


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 防御式幂等：列/索引已存在则跳过（避免重复升级报错）。
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("users")}
    if "feishu_open_id" not in cols:
        op.add_column(
            "users",
            sa.Column("feishu_open_id", sa.String(length=128), nullable=True),
        )
    idx = {i["name"] for i in insp.get_indexes("users")}
    if "ix_users_feishu_open_id" not in idx:
        op.create_index(
            "ix_users_feishu_open_id",
            "users",
            ["feishu_open_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    idx = {i["name"] for i in insp.get_indexes("users")}
    if "ix_users_feishu_open_id" in idx:
        op.drop_index("ix_users_feishu_open_id", table_name="users")
    cols = {c["name"] for c in insp.get_columns("users")}
    if "feishu_open_id" in cols:
        op.drop_column("users", "feishu_open_id")
