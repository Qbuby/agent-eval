"""Add attempts_made column to test_results.

Records how many invocation attempts (initial try + retries) the runner needed
for each case. 1 = succeeded on first try, N>1 = N-1 retries before terminal
state. Surfaced in the UI as a flakiness signal per-case, and aggregated into
test_runs.summary_scores.retry_stats at run completion.
"""
from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_results",
        sa.Column(
            "attempts_made",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("test_results", "attempts_made")
