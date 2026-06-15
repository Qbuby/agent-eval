"""add category column to candidate_cases

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-12

Background
----------
备选数据集（candidate_cases）支持「类别」管理，并在 promote 到基准时按类别名
同步（有则入无则增）。候选的 project_id 可空、类别是项目级强绑 project 的，故
候选侧的类别用**自由文本类别名**存（String(128)，可空，不引 FK），promote 时
再按名在目标 project 的 categories 表里匹配 / 创建。

逐列对照 db_models/tables.py 的 CandidateCaseRow.category。
"""

from alembic import op
import sqlalchemy as sa


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 防御式幂等：列已存在则跳过（避免重复升级报错）。
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("candidate_cases")}
    if "category" not in cols:
        op.add_column(
            "candidate_cases",
            sa.Column("category", sa.String(length=128), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("candidate_cases")}
    if "category" in cols:
        op.drop_column("candidate_cases", "category")
