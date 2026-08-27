"""add durable OmniAgent product plane

Revision ID: 0043
Revises: 0042
Create Date: 2026-08-25

The schema is deliberately frozen in this migration. Do not import current ORM
metadata here: historical migrations must not change when application models do.
"""
from __future__ import annotations

from collections.abc import Callable

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

_INTERNAL_TENANT_ID = "00000000-0000-0000-0000-000000000001"
_TABLES = (
    "omniagent_events",
    "omniagent_jobs",
    "omniagent_job_attempts",
    "omniagent_actions",
    "omniagent_artifacts",
    "omniagent_memories",
    "omniagent_schedules",
    "omniagent_notifications",
    "omniagent_outbox",
    "omniagent_quota_ledger",
)


def _uuid(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, UUID(as_uuid=True), nullable=nullable)


def _tenant() -> sa.Column:
    return sa.Column(
        "tenant_id",
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id"),
        nullable=False,
        server_default=_INTERNAL_TENANT_ID,
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def _indexes(table: str, definitions: tuple[tuple[str, tuple[str, ...]], ...]) -> None:
    for name, columns in definitions:
        op.create_index(name, table, list(columns))


def _create_events() -> None:
    op.create_table(
        "omniagent_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_chat_sessions.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_chat_messages.id", ondelete="SET NULL"),
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(32)),
        sa.Column("entity_id", sa.String(128)),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _created_at(),
        _tenant(),
    )
    _indexes(
        "omniagent_events",
        (
            ("ix_omniagent_events_entity_type", ("entity_type",)),
            ("ix_omniagent_events_entity_id", ("entity_id",)),
            ("ix_omniagent_events_tenant_id", ("tenant_id",)),
            ("ix_omniagent_events_owner_cursor", ("tenant_id", "user_id", "id")),
            ("ix_omniagent_events_session_cursor", ("tenant_id", "session_id", "id")),
            ("ix_omniagent_events_session_id", ("session_id",)),
            ("ix_omniagent_events_event_type", ("event_type",)),
            ("ix_omniagent_events_created_at", ("created_at",)),
            ("ix_omniagent_events_user_id", ("user_id",)),
        ),
    )


def _create_jobs() -> None:
    op.create_table(
        "omniagent_jobs",
        _uuid("id", nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("spec", JSONB(), nullable=False),
        sa.Column("result", JSONB()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "requested_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_chat_sessions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_chat_messages.id", ondelete="SET NULL"),
        ),
        _uuid("action_id"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("cost_estimate", JSONB()),
        sa.Column("usage", JSONB()),
        _created_at(),
        _updated_at(),
        _tenant(),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "omniagent_jobs",
        (
            ("ix_omniagent_jobs_status", ("status",)),
            ("ix_omniagent_jobs_requested_by", ("requested_by",)),
            ("ix_omniagent_jobs_available_at", ("available_at",)),
            ("ix_omniagent_jobs_expires_at", ("expires_at",)),
            ("ix_omniagent_jobs_claim", ("status", "available_at", "priority", "created_at")),
            ("ix_omniagent_jobs_kind", ("kind",)),
            ("ix_omniagent_jobs_lease_owner", ("lease_owner",)),
            ("ix_omniagent_jobs_lease_expires_at", ("lease_expires_at",)),
            ("ix_omniagent_jobs_owner_recent", ("tenant_id", "requested_by", "created_at")),
            ("ix_omniagent_jobs_session_id", ("session_id",)),
            ("ix_omniagent_jobs_action_id", ("action_id",)),
            ("ix_omniagent_jobs_tenant_id", ("tenant_id",)),
        ),
    )


def _create_job_attempts() -> None:
    op.create_table(
        "omniagent_job_attempts",
        _uuid("id", nullable=False),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("runtime_ref", sa.String(256)),
        sa.Column("status", sa.String(24), nullable=False, server_default="running"),
        sa.Column("infrastructure_failure", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        _tenant(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_omniagent_job_attempt_no"),
    )
    _indexes(
        "omniagent_job_attempts",
        (
            ("ix_omniagent_job_attempts_tenant_id", ("tenant_id",)),
            ("ix_omniagent_job_attempts_job_id", ("job_id",)),
        ),
    )


def _create_actions() -> None:
    op.create_table(
        "omniagent_actions",
        _uuid("id", nullable=False),
        sa.Column("capability", sa.String(96), nullable=False),
        sa.Column("arguments", JSONB(), nullable=False),
        sa.Column("argument_digest", sa.String(64), nullable=False),
        sa.Column("risk", sa.String(16), nullable=False),
        sa.Column("impact_preview", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("cost_estimate", JSONB()),
        sa.Column("state", sa.String(24), nullable=False, server_default="prepared"),
        sa.Column(
            "requested_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_chat_sessions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_chat_messages.id", ondelete="SET NULL"),
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "approved_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("denied_at", sa.DateTime(timezone=True)),
        sa.Column("execution_started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "job_id", UUID(as_uuid=True), sa.ForeignKey("omniagent_jobs.id", ondelete="SET NULL")
        ),
        sa.Column("terminal_summary", JSONB()),
        _created_at(),
        _updated_at(),
        _tenant(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_omniagent_action_idempotency"
        ),
    )
    _indexes(
        "omniagent_actions",
        (
            ("ix_omniagent_actions_state", ("state",)),
            ("ix_omniagent_actions_requested_by", ("requested_by",)),
            ("ix_omniagent_actions_tenant_id", ("tenant_id",)),
            ("ix_omniagent_actions_owner_state", ("tenant_id", "requested_by", "state")),
            ("ix_omniagent_actions_capability", ("capability",)),
            ("ix_omniagent_actions_job_id", ("job_id",)),
            ("ix_omniagent_actions_session_id", ("session_id",)),
            ("ix_omniagent_actions_expires_at", ("expires_at",)),
        ),
    )


def _create_artifacts() -> None:
    op.create_table(
        "omniagent_artifacts",
        _uuid("id", nullable=False),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_chat_sessions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "job_id", UUID(as_uuid=True), sa.ForeignKey("omniagent_jobs.id", ondelete="SET NULL")
        ),
        sa.Column("state", sa.String(24), nullable=False, server_default="uploading"),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("extension", sa.String(32), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64)),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("scan_result", JSONB()),
        sa.Column("retention", sa.String(16), nullable=False, server_default="temporary"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True)),
        _created_at(),
        _updated_at(),
        _tenant(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    _indexes(
        "omniagent_artifacts",
        (
            ("ix_omniagent_artifacts_session_id", ("session_id",)),
            ("ix_omniagent_artifacts_sha256", ("sha256",)),
            ("ix_omniagent_artifacts_owner_id", ("owner_id",)),
            ("ix_omniagent_artifacts_expires_at", ("expires_at",)),
            ("ix_omniagent_artifacts_tenant_id", ("tenant_id",)),
            ("ix_omniagent_artifacts_owner_state", ("tenant_id", "owner_id", "state")),
            ("ix_omniagent_artifacts_job_id", ("job_id",)),
            ("ix_omniagent_artifacts_state", ("state",)),
            ("ix_omniagent_artifacts_expiry", ("state", "expires_at")),
        ),
    )


def _create_memories() -> None:
    op.create_table(
        "omniagent_memories",
        _uuid("id", nullable=False),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column(
            "source_action_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_actions.id", ondelete="SET NULL"),
        ),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        _tenant(),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "omniagent_memories",
        (
            ("ix_omniagent_memories_owner_active", ("tenant_id", "owner_id", "deleted_at")),
            ("ix_omniagent_memories_tenant_id", ("tenant_id",)),
            ("ix_omniagent_memories_deleted_at", ("deleted_at",)),
            ("ix_omniagent_memories_owner_id", ("owner_id",)),
        ),
    )


def _create_schedules() -> None:
    op.create_table(
        "omniagent_schedules",
        _uuid("id", nullable=False),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("capability", sa.String(96), nullable=False),
        sa.Column("arguments", JSONB(), nullable=False),
        sa.Column("argument_digest", sa.String(64), nullable=False),
        sa.Column("schedule", JSONB(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "approved_action_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_actions.id"),
            nullable=False,
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        _created_at(),
        _updated_at(),
        _tenant(),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "omniagent_schedules",
        (
            ("ix_omniagent_schedules_tenant_id", ("tenant_id",)),
            ("ix_omniagent_schedules_next_run_at", ("next_run_at",)),
            ("ix_omniagent_schedules_owner_id", ("owner_id",)),
            ("ix_omniagent_schedules_due", ("enabled", "next_run_at")),
            ("ix_omniagent_schedules_owner", ("tenant_id", "owner_id", "enabled")),
        ),
    )


def _create_notifications() -> None:
    op.create_table(
        "omniagent_notifications",
        _uuid("id", nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column(
            "event_id", sa.BigInteger(), sa.ForeignKey("omniagent_events.id", ondelete="SET NULL")
        ),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link", sa.String(1024)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        _created_at(),
        _tenant(),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "omniagent_notifications",
        (
            ("ix_omniagent_notifications_read_at", ("read_at",)),
            ("ix_omniagent_notifications_tenant_id", ("tenant_id",)),
            ("ix_omniagent_notifications_event_id", ("event_id",)),
            ("ix_omniagent_notifications_owner_unread", ("tenant_id", "user_id", "read_at")),
            ("ix_omniagent_notifications_user_id", ("user_id",)),
        ),
    )


def _create_outbox() -> None:
    op.create_table(
        "omniagent_outbox",
        _uuid("id", nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column(
            "notification_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("destination", sa.String(256), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_error", sa.Text()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        _created_at(),
        _tenant(),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "omniagent_outbox",
        (
            ("ix_omniagent_outbox_status", ("status",)),
            ("ix_omniagent_outbox_notification_id", ("notification_id",)),
            ("ix_omniagent_outbox_user_id", ("user_id",)),
            ("ix_omniagent_outbox_tenant_id", ("tenant_id",)),
            ("ix_omniagent_outbox_next_attempt_at", ("next_attempt_at",)),
            ("ix_omniagent_outbox_delivery", ("status", "next_attempt_at")),
        ),
    )


def _create_quota_ledger() -> None:
    op.create_table(
        "omniagent_quota_ledger",
        _uuid("id", nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("entry_type", sa.String(24), nullable=False),
        sa.Column("resource_type", sa.String(32)),
        sa.Column("resource_id", sa.String(128)),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", JSONB()),
        _created_at(),
        _tenant(),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes(
        "omniagent_quota_ledger",
        (
            (
                "ix_omniagent_quota_owner_metric",
                ("tenant_id", "user_id", "metric", "created_at"),
            ),
            ("ix_omniagent_quota_ledger_bucket_start", ("bucket_start",)),
            ("ix_omniagent_quota_ledger_tenant_id", ("tenant_id",)),
            ("ix_omniagent_quota_ledger_user_id", ("user_id",)),
        ),
    )


_CREATORS: tuple[tuple[str, Callable[[], None]], ...] = (
    ("omniagent_events", _create_events),
    ("omniagent_jobs", _create_jobs),
    ("omniagent_job_attempts", _create_job_attempts),
    ("omniagent_actions", _create_actions),
    ("omniagent_artifacts", _create_artifacts),
    ("omniagent_memories", _create_memories),
    ("omniagent_schedules", _create_schedules),
    ("omniagent_notifications", _create_notifications),
    ("omniagent_outbox", _create_outbox),
    ("omniagent_quota_ledger", _create_quota_ledger),
)


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for name, create in _CREATORS:
        if name not in existing:
            create()
            existing.add(name)


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for name in reversed(_TABLES):
        if name in existing:
            op.drop_table(name)