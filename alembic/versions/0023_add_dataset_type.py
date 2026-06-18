"""add dataset_type column to dataset_metadata

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-18

Background
----------
数据集需要区分「类型」，以隔离两类互不相干的数据集：
- candidate：备选数据集（默认，原有的单轮问答 / 候选样例管理）
- conversation：多轮对话集（多轮对话评估样例）

权威类型存在我们自己控制、带租户隔离的本地表 dataset_metadata（不依赖
LangSmith 自身 metadata —— 后者读写经 LangSmith API，DNS/同步不可靠，不适合
做列表过滤的权威源）。

向后兼容：老数据集在 dataset_metadata 没有行 → 列表过滤时一律视为 candidate，
继续显示在备选数据集页，不丢数据。故新列默认 'candidate'。

逐列对照 db_models/tables.py 的 DatasetMetadataRow.dataset_type。
"""

from alembic import op
import sqlalchemy as sa


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 防御式幂等：列已存在则跳过（避免重复升级报错）。
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("dataset_metadata")}
    if "dataset_type" not in cols:
        op.add_column(
            "dataset_metadata",
            sa.Column(
                "dataset_type",
                sa.String(length=16),
                nullable=False,
                server_default="candidate",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("dataset_metadata")}
    if "dataset_type" in cols:
        op.drop_column("dataset_metadata", "dataset_type")
