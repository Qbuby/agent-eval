"""Add ``expected_output`` to test_results.

The expected/reference answer was computed by the runner for scoring but
never persisted on the result row (``create_test_result`` didn't pass it,
and the column didn't exist). Exports and the detail view therefore had no
期望答案 to show, and it couldn't be recovered for runs whose originating
case rows are gone or whose ``benchmark_case_id`` is NULL.

This adds a nullable Text column. The runner snapshots the expected answer
into it at persist time (benchmark ``reference_answer`` or uploaded
``expected_output``). Rows created before this migration stay NULL — there
is no reliable source to backfill them from.
"""
from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_results",
        sa.Column("expected_output", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("test_results", "expected_output")
