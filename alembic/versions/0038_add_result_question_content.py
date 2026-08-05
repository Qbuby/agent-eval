"""freeze multimodal question content on evaluation results

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-05

带附件样例进入评估后，test_results.question 只保存纯文本投影，结果详情无法恢复
原始图片。本迁移增加可空 JSONB 快照列：新评估冻结 canonical content blocks，
纯文本与存量结果保持 NULL。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

_TABLE = "test_results"
_COLUMN = "question_content"


def _has_column(insp, table: str, column: str) -> bool:
    return column in {item["name"] for item in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _TABLE in insp.get_table_names() and not _has_column(insp, _TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, JSONB(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _TABLE in insp.get_table_names() and _has_column(insp, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
