"""Add evaluator_providers table for LLM-judge API credentials.

Stores per-provider config (OpenAI/Anthropic/DeepSeek/Azure/custom) that
configurable LLM-judge evaluators reference by id. API keys are stored
fernet-encrypted (see ``agent_eval.evaluation.crypto``); the column is
``BYTEA`` and never appears in API responses in plaintext.

Referenced from ``evaluator_configs.params -> 'provider_id'`` (string UUID)
once 0014 lands. Kept loosely coupled (no FK) so deleting a provider
gracefully degrades the evaluator to "unconfigured" rather than cascading.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluator_providers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("provider_type", sa.String(32), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("default_model", sa.String(128), nullable=True),
        sa.Column(
            "extra_config",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_evaluator_providers_provider_type",
        "evaluator_providers",
        ["provider_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_evaluator_providers_provider_type", table_name="evaluator_providers")
    op.drop_table("evaluator_providers")
