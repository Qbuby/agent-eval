"""widen evaluation_scores.dimension from 64 to 255

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-25

Background
----------
多轮对话评估的逐轮 / 会话级打分用复合 score key 作 dimension：
``{label}.turn{n}``（逐轮）与 ``{label}.conversation``（会话级），其中 label
是评估器显示名，可由用户自定义、长度不受约束。原列 ``String(64)`` 在 label
稍长时即超长，flush 抛 ``value too long for type character varying(64)``。

致命之处：该 flush 与 ``create_test_result`` 同处一个 session（langfuse_runner
的 _do_one），一旦超长抛错，整个 test_result 行连同已 flush 的 score 一起回滚
——「跑了但没存下」。扩到 255 与单轮的人类可读 label 上限一致，给多轮复合 key
留足空间。

幂等：列类型已是 varchar(255)+ 则跳过。索引 ``ix_evaluation_scores_dimension``
在 ALTER TYPE 时由 Postgres 自动保留，无需重建。
"""

from alembic import op
import sqlalchemy as sa


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def _dimension_len(bind) -> int | None:
    insp = sa.inspect(bind)
    for c in insp.get_columns("evaluation_scores"):
        if c["name"] == "dimension":
            t = c["type"]
            return getattr(t, "length", None)
    return None


def upgrade() -> None:
    bind = op.get_bind()
    if (_dimension_len(bind) or 0) < 255:
        op.alter_column(
            "evaluation_scores",
            "dimension",
            existing_type=sa.String(64),
            type_=sa.String(255),
            existing_nullable=False,
        )


def downgrade() -> None:
    # 缩回 64 可能截断已存的长 key，故 downgrade 只在确实是 255 时执行，
    # 且依赖调用方自行清理超长行——一般不建议回退。
    bind = op.get_bind()
    if (_dimension_len(bind) or 0) > 64:
        op.alter_column(
            "evaluation_scores",
            "dimension",
            existing_type=sa.String(255),
            type_=sa.String(64),
            existing_nullable=False,
        )
