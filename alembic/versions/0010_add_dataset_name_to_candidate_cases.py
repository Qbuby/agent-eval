"""Add dataset_name column to candidate_cases.

candidate_cases was created in 0006 keyed by project_id, but later changes added
a dataset_name column to the ORM model and the candidates router/import paths
without a corresponding migration. As a result every candidate insert and every
filter-by-dataset_name query was broken: import-langsmith silently failed and
the dataset detail page returned an empty list.

This migration adds the missing column + index. No backfill — historical
candidate_cases is empty.
"""
from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_cases",
        sa.Column("dataset_name", sa.String(256), nullable=True),
    )
    op.create_index(
        "ix_candidate_cases_dataset_name",
        "candidate_cases",
        ["dataset_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_cases_dataset_name", table_name="candidate_cases")
    op.drop_column("candidate_cases", "dataset_name")
