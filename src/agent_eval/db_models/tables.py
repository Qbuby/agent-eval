from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


class DatasetVersionRow(Base):
    __tablename__ = "dataset_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    version_tag: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parent_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TestCaseRow(Base):
    __tablename__ = "test_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_versions.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")

    input_messages: Mapped[dict] = mapped_column(JSONB, nullable=False)
    agent_config_override: Mapped[dict | None] = mapped_column(JSONB)

    expected_output: Mapped[str | None] = mapped_column(Text)
    expected_output_criteria: Mapped[list] = mapped_column(JSONB, default=list)
    expected_tool_calls: Mapped[list] = mapped_column(JSONB, default=list)
    max_tool_calls: Mapped[int | None] = mapped_column(Integer)
    max_latency_ms: Mapped[int | None] = mapped_column(Integer)
    max_tokens: Mapped[int | None] = mapped_column(Integer)

    eval_weights: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scoring_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="hybrid")

    parent_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_cases.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TestRunRow(Base):
    __tablename__ = "test_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_versions.id"), nullable=False
    )
    agent_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    optimization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ab_group: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_scores: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TestResultRow(Base):
    __tablename__ = "test_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_runs.id"), nullable=False, index=True
    )
    test_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_cases.id"), nullable=False
    )

    actual_output: Mapped[str | None] = mapped_column(Text)
    actual_tool_calls: Mapped[list | None] = mapped_column(JSONB)
    full_trace: Mapped[dict | None] = mapped_column(JSONB)

    latency_ms: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    tool_call_count: Mapped[int | None] = mapped_column(Integer)

    error_message: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(String(64))

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EvaluationScoreRow(Base):
    __tablename__ = "evaluation_scores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_results.id"), nullable=False, index=True
    )
    dimension: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    weight: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    weighted_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    scoring_method: Mapped[str] = mapped_column(String(16), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OptimizationRow(Base):
    __tablename__ = "optimizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    source_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_runs.id"), nullable=False
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_analysis: Mapped[dict] = mapped_column(JSONB, nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_detail: Mapped[dict] = mapped_column(JSONB, nullable=False)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_runs.id")
    )
    improvement_delta: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RefreshTokenRow(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SystemConfigRow(Base):
    __tablename__ = "system_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="general")
    description: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RoutingRuleRow(Base):
    __tablename__ = "routing_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    source_project: Mapped[str] = mapped_column(String(256), nullable=False)
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    target_dataset: Mapped[str] = mapped_column(String(256), nullable=False)
    transform_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RoutingLogRow(Base):
    __tablename__ = "routing_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("routing_rules.id"), index=True
    )
    run_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    source_project: Mapped[str] = mapped_column(String(256), nullable=False)
    target_dataset: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditLogRow(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class ExampleFingerprintRow(Base):
    __tablename__ = "example_fingerprints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    dataset_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    example_id: Mapped[str] = mapped_column(String(256), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TraceWatchCursorRow(Base):
    __tablename__ = "trace_watch_cursors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    last_seen_run_id: Mapped[str | None] = mapped_column(String(256))
    last_seen_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runs_fetched_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class LoopControlLogRow(Base):
    __tablename__ = "loop_control_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    loop_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_runs.id")
    )
    optimization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("optimizations.id")
    )
    aggregate_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    target_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    converged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    safety_stopped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
