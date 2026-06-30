"""add langfuse_trace_name column to test_runs

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-22

Background
----------
Langfuse 侧需要一条与 LangSmith ``langsmith_project`` 对称的「回拉键」。

LangSmith 的回拉用 project 名 + 时间窗 + question 文本匹配，把
``test_results.langsmith_run_id`` 回贴。Langfuse 的项目由连接预设的
public/secret key 对绑定（不是查询参数），所以 per-run 的回拉键是 trace
的 **name**：发起评估时把这个 name 透传给被测 agent（agent 自己上报 trace
时带上），评估结束后按 ``name + fromTimestamp/toTimestamp`` 拉 trace、按
question 文本匹配，回贴 ``test_results.langfuse_trace_id``。

为什么不复用 ``langfuse_run_name``：那一列是给运行历史列表展示/搜索用的运行
名，语义不同、值也不同（默认是 ``eval-<时间戳>``）。回拉键必须独立成列，否
则二者会互相污染。

向后兼容：老运行该列为 NULL → 发起页不传 trace name → 不触发 Langfuse 回拉，
行为与现状一致。

逐列对照 db_models/tables.py 的 TestRunRow.langfuse_trace_name。
"""

from alembic import op
import sqlalchemy as sa


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 防御式幂等：列已存在则跳过（避免重复升级报错）。
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("test_runs")}
    if "langfuse_trace_name" not in cols:
        op.add_column(
            "test_runs",
            sa.Column("langfuse_trace_name", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("test_runs")}
    if "langfuse_trace_name" in cols:
        op.drop_column("test_runs", "langfuse_trace_name")
