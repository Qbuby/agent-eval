"""Add benchmark and candidate tables

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "categories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "name", name="uq_category_project_name"),
    )

    op.create_table(
        "benchmark_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("version_tag", sa.String(64), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("case_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "version_tag", name="uq_benchmark_version_project_tag"),
    )

    op.create_table(
        "benchmark_cases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("category_id", UUID(as_uuid=True), sa.ForeignKey("categories.id", ondelete="SET NULL"), index=True),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("benchmark_versions.id", ondelete="SET NULL"), index=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("reference_answer", sa.Text),
        sa.Column("key_points", JSONB, nullable=False, server_default="[]"),
        sa.Column("negative_points", JSONB, nullable=False, server_default="[]"),
        sa.Column("tags", ARRAY(sa.Text), server_default="{}"),
        sa.Column("difficulty", sa.String(16)),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("source_case_id", UUID(as_uuid=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "candidate_cases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="SET NULL"), index=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", sa.Text),
        sa.Column("key_points", JSONB),
        sa.Column("negative_points", JSONB),
        sa.Column("tags", ARRAY(sa.Text), server_default="{}"),
        sa.Column("extra_metadata", JSONB),
        sa.Column("langsmith_example_id", sa.String(256)),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending", index=True),
        sa.Column("reviewed_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "import_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("file_type", sa.String(16), nullable=False),
        sa.Column("total_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("imported_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pending_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="completed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("import_batches")
    op.drop_table("candidate_cases")
    op.drop_table("benchmark_cases")
    op.drop_table("benchmark_versions")
    op.drop_table("categories")
    op.drop_table("projects")
