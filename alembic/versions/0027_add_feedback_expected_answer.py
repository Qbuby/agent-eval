"""add expected_answer to sample_feedbacks（评审人填写的期望答案）

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-29

Background
----------
样例评审页给评审人员加「期望答案」（参考答案/标准答案）填写入口。期望答案与
打分/意见同属评审产出，故并入既有 ``sample_feedbacks`` 表，复用
``(sample_id, rated_by)`` 的 upsert：每位评审各存一份，随分页样例带出。

只加一列 ``expected_answer TEXT NULL``。幂等：列已存在则跳过。
"""

from alembic import op
import sqlalchemy as sa


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "sample_feedbacks" not in insp.get_table_names():
        return
    if _has_column(insp, "sample_feedbacks", "expected_answer"):
        return
    op.add_column(
        "sample_feedbacks",
        sa.Column("expected_answer", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "sample_feedbacks" not in insp.get_table_names():
        return
    if not _has_column(insp, "sample_feedbacks", "expected_answer"):
        return
    op.drop_column("sample_feedbacks", "expected_answer")
