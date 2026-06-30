"""Add first-token-latency columns to test_results.

Two new metrics, both nullable INTEGER milliseconds relative to the agent
invoke start time:

* ``first_thinking_token_ms`` — first streamed text byte from the agent's
  *first* LLM step. Tells you how long until any reasoning showed up.
* ``first_answer_token_ms``   — first streamed text byte of the LLM step
  that produced the user-visible answer. For tool-using agents this is the
  meaningful TTFT (loops through tools then begins answering).

Filled in by ``SSEStreamAdapter`` per-step and aggregated by the runner; the
columns stay NULL on older rows so historical runs render an em-dash.
"""
from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_results",
        sa.Column("first_thinking_token_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "test_results",
        sa.Column("first_answer_token_ms", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("test_results", "first_answer_token_ms")
    op.drop_column("test_results", "first_thinking_token_ms")
