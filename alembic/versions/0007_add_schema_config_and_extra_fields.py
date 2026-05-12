"""Add schema_config to categories and extra_fields to benchmark_cases

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("categories", sa.Column("schema_config", JSONB, nullable=True))
    op.add_column("benchmark_cases", sa.Column("extra_fields", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("benchmark_cases", "extra_fields")
    op.drop_column("categories", "schema_config")
