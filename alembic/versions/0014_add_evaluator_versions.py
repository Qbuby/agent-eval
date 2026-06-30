"""Add evaluator_versions table + current_version_id link.

Configurable LLM-judge evaluators need version pinning so that historical
runs reproduce the exact judge prompt / model / dimensions used at run
time, even after the evaluator config is later edited.

- ``evaluator_versions``: append-only snapshot per save. ``version_number``
  is a per-evaluator monotonic counter; ``params`` holds the full config
  snapshot (provider_id, model, prompt_template, dimensions, ...).
- ``evaluator_configs.current_version_id``: pointer to the latest published
  version. Nullable so legacy rows (created before versioning landed) keep
  working without a backfill — they simply have no version history.
- Run-time pinning: a test run snapshots ``evaluator_version_id`` into its
  ``test_runs.evaluator_configs[]`` JSON entry, so langfuse_runner can pick
  the exact prompt/model when reproducing.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluator_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "evaluator_id",
            UUID(as_uuid=True),
            sa.ForeignKey("evaluator_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "params",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "evaluator_id", "version_number",
            name="uq_evaluator_versions_evaluator_id_version_number",
        ),
    )
    op.create_index(
        "ix_evaluator_versions_evaluator_id",
        "evaluator_versions",
        ["evaluator_id"],
    )

    op.add_column(
        "evaluator_configs",
        sa.Column(
            "current_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("evaluator_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("evaluator_configs", "current_version_id")
    op.drop_index(
        "ix_evaluator_versions_evaluator_id", table_name="evaluator_versions"
    )
    op.drop_table("evaluator_versions")
