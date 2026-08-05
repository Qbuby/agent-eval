"""carry multimodal content blocks on portal samples

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-05

客户 Portal 的样例（``portal_samples``）此前只有 Text 的 question 列，客户把
截图贴在 xlsx 里上传时图片被整条丢掉——内部单轮两张表已在 0037 补过
question_content，本迁移把同一形状补到 Portal 侧，语义完全一致：

- ``question``：恒为纯文本投影（附件渲染成 ``[图片]`` 占位）。评审列表摘要、
  搜索、内部反馈复盘页都直接读它，一行都不用改。
- ``question_content``：仅在带附件时存 canonical blocks，纯文本样例存 NULL，
  故存量行无需回填。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

_TABLE = "portal_samples"
_COLUMN = "question_content"


def _has_column(insp, table: str, column: str) -> bool:
    return column in {item["name"] for item in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _TABLE in set(insp.get_table_names()) and not _has_column(insp, _TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, JSONB(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _TABLE in set(insp.get_table_names()) and _has_column(insp, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
