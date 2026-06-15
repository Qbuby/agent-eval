from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agent_eval.db_models.tenant_context import INTERNAL_TENANT_ID


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


class TenantMixin:
    """给「客户可分离数据」表挂租户列。

    继承它的表会被 db.py 注册的事件监听器自动过滤（读）/盖章（写）：
    - 读：非 superadmin 且有租户上下文时，查询自动加 ``tenant_id == ctx``。
    - 写：新行 tenant_id 为空时，由 before_flush 补当前租户或内部 sentinel。

    所以业务代码通常无需手写 tenant_id，靠监听器兜底。default 给
    INTERNAL_TENANT_ID 是双保险：即便监听器未触发（极端情况），也不会写出
    NULL 违反 NOT NULL 约束。
    """

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
        default=INTERNAL_TENANT_ID,
    )


class DatasetVersionRow(Base, TenantMixin):
    __tablename__ = "dataset_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    version_tag: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parent_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TestCaseRow(Base, TenantMixin):
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


class TestRunRow(Base, TenantMixin):
    __tablename__ = "test_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    dataset_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_versions.id"), nullable=True
    )
    benchmark_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("benchmark_versions.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    eval_case_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_case_sources.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    agent_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    optimization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ab_group: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eval_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_scores: Mapped[dict | None] = mapped_column(JSONB)
    langfuse_run_name: Mapped[str | None] = mapped_column(Text)
    langsmith_project: Mapped[str | None] = mapped_column(Text)
    evaluator_configs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Soft-delete: list endpoints filter out non-null deleted_at by default;
    # the row stays in DB so historical reports / langfuse links still work.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TestResultRow(Base, TenantMixin):
    __tablename__ = "test_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_runs.id"), nullable=False, index=True
    )
    test_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_cases.id"), nullable=True
    )
    benchmark_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("benchmark_cases.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    question: Mapped[str | None] = mapped_column(Text)
    # Expected/reference answer, snapshotted at run time from the case source
    # (benchmark reference_answer or uploaded expected_output). Persisted here
    # so the export/detail view always has it, independent of whether the
    # originating case still exists. NULL on rows created before 0016.
    expected_output: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(Text)
    langsmith_run_id: Mapped[str | None] = mapped_column(Text)

    actual_output: Mapped[str | None] = mapped_column(Text)
    actual_tool_calls: Mapped[list | None] = mapped_column(JSONB)
    full_trace: Mapped[dict | None] = mapped_column(JSONB)
    langfuse_trace_id: Mapped[str | None] = mapped_column(Text)

    latency_ms: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    # Anthropic-style prompt-cache breakdown. cache_creation_tokens are
    # paid-once tokens that seeded the cache; cache_read_tokens are tokens
    # served from cache on the same request (i.e. cache hits, the cheap
    # ones). Sample-level surface so the detail page can show per-case
    # cache yield, not just the run-level avg.
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    tool_call_count: Mapped[int | None] = mapped_column(Integer)

    # Time-to-first-token (ms, relative to invoke start). Two flavours:
    #   first_thinking_token_ms — first text byte from the agent's *first*
    #     LLM step. For non-tool-using agents, this equals the answer's TTFT;
    #     for tool-using agents, it's how long until reasoning began streaming.
    #   first_answer_token_ms   — first text byte of the *final* LLM step
    #     (the one promoted to type='answer' by SSEStreamAdapter). For agents
    #     that loop through tools then answer, this is the user-perceived TTFT.
    first_thinking_token_ms: Mapped[int | None] = mapped_column(Integer)
    first_answer_token_ms: Mapped[int | None] = mapped_column(Integer)

    error_message: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(String(64))

    attempts_made: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EvaluationScoreRow(Base, TenantMixin):
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


class OptimizationRow(Base, TenantMixin):
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
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # users 表本身不挂 TenantMixin（admin 要跨租户管用户，不能被过滤），
    # 但每个用户归属一个租户：外部客户落自己的租户，内部用户落 sentinel。
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
        default=INTERNAL_TENANT_ID,
    )
    # superadmin = 内部 admin，监听器对其读查询不注入租户过滤（全租户可见）。
    is_superadmin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


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


class RoutingRuleRow(Base, TenantMixin):
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


class RoutingLogRow(Base, TenantMixin):
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


class DatasetMetadataRow(Base, TenantMixin):
    __tablename__ = "dataset_metadata"
    __table_args__ = (
        UniqueConstraint("dataset_name", name="uq_dataset_metadata_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    dataset_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    source_project: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    max_examples: Mapped[int | None] = mapped_column(Integer)
    retention_policy: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditLogRow(Base, TenantMixin):
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


class ExampleFingerprintRow(Base, TenantMixin):
    __tablename__ = "example_fingerprints"
    __table_args__ = (
        UniqueConstraint("dataset_name", "fingerprint", name="uq_dataset_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    dataset_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    example_id: Mapped[str] = mapped_column(String(256), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TraceWatchCursorRow(Base, TenantMixin):
    __tablename__ = "trace_watch_cursors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    last_seen_run_id: Mapped[str | None] = mapped_column(String(256))
    last_seen_run_ids: Mapped[list | None] = mapped_column(JSONB)
    last_seen_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runs_fetched_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class LoopControlLogRow(Base, TenantMixin):
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


# ---------------------------------------------------------------------------
# Benchmark & Candidate tables
# ---------------------------------------------------------------------------


class ProjectRow(Base, TenantMixin):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CategoryRow(Base, TenantMixin):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    schema_config: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_category_project_name"),
    )


class BenchmarkVersionRow(Base, TenantMixin):
    __tablename__ = "benchmark_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "version_tag", name="uq_benchmark_version_project_tag"),
    )


class BenchmarkCaseRow(Base, TenantMixin):
    __tablename__ = "benchmark_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("benchmark_versions.id", ondelete="SET NULL"), index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    reference_answer: Mapped[str | None] = mapped_column(Text)
    key_points: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    negative_points: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    difficulty: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    source_case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    extra_fields: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CandidateCaseRow(Base, TenantMixin):
    __tablename__ = "candidate_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    dataset_name: Mapped[str | None] = mapped_column(String(256), index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    # 自由文本类别名（不绑 project 的 CategoryRow）。promote 时按名同步到目标
    # project 的 categories（有则复用、无则新增），见 candidates.promote。
    category: Mapped[str | None] = mapped_column(String(128), index=True)
    key_points: Mapped[list | None] = mapped_column(JSONB)
    negative_points: Mapped[list | None] = mapped_column(JSONB)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB)
    langsmith_example_id: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ImportBatchRow(Base, TenantMixin):
    __tablename__ = "import_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EvalCaseSourceRow(Base, TenantMixin):
    """Ephemeral per-upload case list (user-provided JSON/JSONL file).

    One row per upload. The ``cases`` JSONB is a list of
    ``{"name": str, "question": str, "expected_keywords": [str]}`` objects,
    matching the shape produced by ``D:/files/EPtestcases/testcases_*.json``.
    """
    __tablename__ = "eval_case_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="file")
    file_format: Mapped[str | None] = mapped_column(String(16))
    cases: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EvaluatorConfigRow(Base, TenantMixin):
    """Named reusable evaluator instances.

    Originally each row carried evaluator_type + params and ran a local
    scoring function. After the 2026-05-19 simplification, evaluators are
    pure tag templates: the user picks one or more, and at run time we
    stamp each row's `tag` onto every sample's Langfuse trace. Langfuse-
    side evaluators (configured in the Langfuse UI) then key off those
    tags. evaluator_type and params remain on the row for backward
    compatibility with historical runs but are no longer required.
    """
    __tablename__ = "evaluator_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    # tag = the literal string we attach to every Langfuse trace this
    # evaluator runs against. Defaults to the row's name; user can override
    # so they can have e.g. name="goal accuracy v2", tag="agent-eval-correctness".
    tag: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluator_type: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Pointer to the most recently published EvaluatorVersionRow. Nullable for
    # legacy rows (pre-versioning) and for evaluators that haven't been saved
    # through the versioned editor yet.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluator_versions.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EvaluatorVersionRow(Base, TenantMixin):
    """Append-only snapshot of an evaluator's config at a point in time.

    Each save through the configurable-judge editor writes a new row;
    ``version_number`` is a per-evaluator monotonic counter starting at 1.
    Runs pin to a specific version via
    ``test_runs.evaluator_configs[].evaluator_version_id`` so historical
    results reproduce against the exact prompt/model used at the time.
    """
    __tablename__ = "evaluator_versions"
    __table_args__ = (
        UniqueConstraint(
            "evaluator_id", "version_number",
            name="uq_evaluator_versions_evaluator_id_version_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    evaluator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluator_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EvaluatorProviderRow(Base, TenantMixin):
    """LLM-judge provider credential record.

    A row represents one usable LLM endpoint (OpenAI / Anthropic / DeepSeek /
    Azure / OpenAI-compatible) the user wants their configurable evaluators to
    call. ``api_key_encrypted`` holds a fernet-encrypted key blob; plaintext
    never touches DB or API responses (see ``evaluation.crypto``).

    Evaluators reference a provider via ``evaluator_configs.params['provider_id']``
    as a UUID string — kept loosely coupled (no FK) so deleting a provider
    leaves historical evaluators in an "unconfigured" state instead of cascading.
    """
    __tablename__ = "evaluator_providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    default_model: Mapped[str | None] = mapped_column(String(128))
    extra_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# 多租户 + 外部客户 Portal 表
# ---------------------------------------------------------------------------


class TenantRow(Base):
    """租户。内部租户是固定 sentinel（id=INTERNAL_TENANT_ID），存量数据全挂它。

    tenants 本身不挂 TenantMixin —— 它是隔离的「维度表」，不能被自身过滤
    （否则普通租户连自己所属的 tenant 行都查不到，admin 也管不了别家）。
    """

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class PortalSampleBatchRow(Base, TenantMixin):
    """外部客户一次 xlsx 上传 = 一个批次。tenant_id 由监听器自动盖章。"""

    __tablename__ = "portal_sample_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)  # 来自文件名
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PortalSampleRow(Base, TenantMixin):
    """解析后的单条 QA 样例。其余 xlsx 列进 extra（JSONB）。"""

    __tablename__ = "portal_samples"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("portal_sample_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SampleFeedbackRow(Base, TenantMixin):
    """客户对单条样例的手动打分 + 意见。一个用户对一个 sample 一条。"""

    __tablename__ = "sample_feedbacks"
    __table_args__ = (
        UniqueConstraint("sample_id", "rated_by", name="uq_sample_feedback_sample_rater"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    sample_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("portal_samples.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    overall: Mapped[int | None] = mapped_column(Integer)  # 总体 1-5
    # 维度→分，如 {relevance, difficulty, answer_accuracy}
    scores: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class EntryCodeRow(Base):
    """注册入口码：用户注册时凭码绑定到某租户并获得指定角色。

    像 TenantRow 一样**不挂 TenantMixin** —— 注册发生在 pre-auth（还没有租户
    上下文），监听器若对其注入过滤会查不到任何码；且内部 admin 要跨租户管理所有
    码。code 为明文（admin 需查看并分发给客户，不同于密码的 bcrypt 哈希）。
    """

    __tablename__ = "entry_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # 凭此码注册的用户落到哪个租户（内部码指向 INTERNAL_TENANT_ID）
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, default=INTERNAL_TENANT_ID
    )
    # 凭此码注册的用户角色：user / external_customer / admin
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    label: Mapped[str | None] = mapped_column(String(128))  # 人类描述，如「中力客户入口」
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class LangfuseTraceMetricRow(Base, TenantMixin):
    """从 Langfuse 周期拉取并持久化的 trace 级聚合指标。

    24h 轮询近 30 天窗口，按 ``langfuse_trace_id`` 幂等 upsert（同一 trace 的
    cost/score 可能被异步补算，全窗口重拉保证最终一致）。挂 TenantMixin：后台
    轮询无租户上下文 → 监听器旁路、写入自动落 INTERNAL_TENANT_ID，内部 admin
    （superadmin）读取不被过滤、跨 env 全见。

    指标来源见 langfuse_metrics/compute.py。cache_* 三列恒 NULL（当前 trace 未
    上报缓存 token，留作占位，前端显示 N/A）。
    """

    __tablename__ = "langfuse_trace_metrics"
    __table_args__ = (
        # 支撑「按 environment + 时间窗筛选」主查询
        Index("ix_lf_trace_env_ts", "environment", "trace_timestamp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    langfuse_trace_id: Mapped[str] = mapped_column(
        String(256), unique=True, nullable=False, index=True
    )
    environment: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(512))
    trace_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    session_id: Mapped[str | None] = mapped_column(String(256))
    user_id: Mapped[str | None] = mapped_column(String(256))
    release: Mapped[str | None] = mapped_column(String(128))
    tags: Mapped[list | None] = mapped_column(JSONB)

    # 总响应时间
    latency_s: Mapped[float | None] = mapped_column(Numeric(12, 4))
    # token 汇总
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    # 成本
    total_cost: Mapped[float | None] = mapped_column(Numeric(12, 6))
    # 首工具调用时间（秒，相对 trace 起点）
    first_tool_call_s: Mapped[float | None] = mapped_column(Numeric(12, 4))
    # 工具调用统计
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_success_rate: Mapped[float | None] = mapped_column(Numeric(5, 4))
    tool_call_counts: Mapped[dict | None] = mapped_column(JSONB)  # {tool_name: count}
    # 首思考 / 首答 token 时间（秒，相对 trace 起点）
    first_thinking_token_s: Mapped[float | None] = mapped_column(Numeric(12, 4))
    first_answer_token_s: Mapped[float | None] = mapped_column(Numeric(12, 4))
    # 缓存（占位，当前恒 NULL）
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_hit_rate: Mapped[float | None] = mapped_column(Numeric(5, 4))
    # 结构计数
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    has_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 详情展示原文
    input: Mapped[dict | None] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB)
    # 列名用 trace_metadata 避开 DeclarativeBase 保留属性名 metadata
    trace_metadata: Mapped[dict | None] = mapped_column(JSONB)
    scores: Mapped[list | None] = mapped_column(JSONB)

    raw_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class LangfuseObservationMetricRow(Base, TenantMixin):
    """Langfuse observation（CHAIN/AGENT/GENERATION/TOOL）明细。

    用业务键 ``langfuse_trace_id`` 关联回 trace（**不建 DB 外键**，避免拉取顺序
    耦合；Langfuse id 全局唯一，index 足够）。冗余存 ``trace_timestamp`` 便于按
    时间独立清理。``output`` 必存——compute 的首思考/首答判别依赖它。
    """

    __tablename__ = "langfuse_observation_metrics"
    __table_args__ = (
        UniqueConstraint("langfuse_observation_id", name="uq_lf_obs_observation_id"),
        # 详情页按 trace 拉明细 + 可按类型筛
        Index("ix_lf_obs_trace_type", "langfuse_trace_id", "type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    langfuse_observation_id: Mapped[str] = mapped_column(
        String(256), nullable=False, index=True
    )
    langfuse_trace_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trace_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(512))
    level: Mapped[str | None] = mapped_column(String(16))
    status_message: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(128))
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_s: Mapped[float | None] = mapped_column(Numeric(12, 4))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    usage_input: Mapped[int | None] = mapped_column(Integer)
    usage_output: Mapped[int | None] = mapped_column(Integer)
    usage_total: Mapped[int | None] = mapped_column(Integer)
    calculated_total_cost: Mapped[float | None] = mapped_column(Numeric(12, 6))
    total_price: Mapped[float | None] = mapped_column(Numeric(12, 6))
    time_to_first_token_s: Mapped[float | None] = mapped_column(Numeric(12, 4))
    completion_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parent_observation_id: Mapped[str | None] = mapped_column(String(256))
    obs_metadata: Mapped[dict | None] = mapped_column(JSONB)
    input: Mapped[dict | None] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class LangfuseMetricsCursorRow(Base, TenantMixin):
    """Langfuse 指标轮询的单例游标 / 运行状态。

    ``scope`` 固定一行（"global"），记录上次成功轮询的 wall-clock 用于进程重启
    补跑判断（距上次 ≥ 间隔才补跑），以及累计 / 最近一轮计数、连续失败、状态。
    """

    __tablename__ = "langfuse_metrics_cursors"
    __table_args__ = (UniqueConstraint("scope", name="uq_lf_metrics_cursor_scope"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default="global")
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    traces_synced_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observations_synced_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_traces: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_observations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
