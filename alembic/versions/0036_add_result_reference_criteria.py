"""freeze answer key points on evaluation results

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-28

答案关键点此前只存在源样例中，评估时没有稳定快照；源样例修改后，补评会失去
首评时的参考依据。本迁移把关键点冻结到 test_results，供首评审计、详情展示和
后续补评复用。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    return column in {item["name"] for item in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if (
        "test_results" in insp.get_table_names()
        and not _has_column(insp, "test_results", "expected_output_criteria")
    ):
        op.add_column(
            "test_results",
            sa.Column(
                "expected_output_criteria",
                JSONB(),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if (
        "test_results" in insp.get_table_names()
        and _has_column(insp, "test_results", "expected_output_criteria")
    ):
        op.drop_column("test_results", "expected_output_criteria")
