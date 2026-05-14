"""add routing_rules and routing_logs tables

Revision ID: 0005a_add_routing_tables
Revises: 0005_add_source_project
Create Date: 2026-05-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005a_add_routing_tables"
down_revision = "0005_add_source_project"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "routing_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("source_project", sa.String(256), nullable=False),
        sa.Column("conditions", JSONB(), nullable=False, server_default="{}"),
        sa.Column("target_dataset", sa.String(256), nullable=False),
        sa.Column("transform_config", JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
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
    op.create_index("ix_routing_rules_priority", "routing_rules", ["priority"])
    op.create_index("ix_routing_rules_source_project", "routing_rules", ["source_project"])

    op.create_table(
        "routing_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rule_id", UUID(as_uuid=True), sa.ForeignKey("routing_rules.id"), nullable=True
        ),
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("source_project", sa.String(256), nullable=False),
        sa.Column("target_dataset", sa.String(256), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_routing_logs_rule_id", "routing_logs", ["rule_id"])
    op.create_index("ix_routing_logs_run_id", "routing_logs", ["run_id"])
    op.create_index("ix_routing_logs_status", "routing_logs", ["status"])
    op.create_index("ix_routing_logs_created_at", "routing_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_routing_logs_created_at", table_name="routing_logs")
    op.drop_index("ix_routing_logs_status", table_name="routing_logs")
    op.drop_index("ix_routing_logs_run_id", table_name="routing_logs")
    op.drop_index("ix_routing_logs_rule_id", table_name="routing_logs")
    op.drop_table("routing_logs")
    op.drop_index("ix_routing_rules_source_project", table_name="routing_rules")
    op.drop_index("ix_routing_rules_priority", table_name="routing_rules")
    op.drop_table("routing_rules")
