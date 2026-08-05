"""carry multimodal content blocks on single-turn cases

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-03

带图样例此前只能落在多轮的 input_messages[*].content 上，单轮两张表的
question 是 Text 列，没有承载附件的位置。本迁移给两表各加一列 question_content
（JSONB，可空）存 canonical content blocks；question 列保持纯文本投影不变，
去重、ilike 搜索、导出列、judge prompt 等既有消费方无需改动。

纯文本样例的 question_content 为 NULL，故存量行不需要回填。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

_TABLES = ("benchmark_cases", "candidate_cases")
_COLUMN = "question_content"


def _has_column(insp, table: str, column: str) -> bool:
    return column in {item["name"] for item in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    for table in _TABLES:
        if table in existing and not _has_column(insp, table, _COLUMN):
            op.add_column(table, sa.Column(_COLUMN, JSONB(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    for table in _TABLES:
        if table in existing and _has_column(insp, table, _COLUMN):
            op.drop_column(table, _COLUMN)
