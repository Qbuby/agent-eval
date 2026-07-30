"""add persisted agent replies (回复版本 + 批量生成任务)

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-27

Background
----------
此前 agent 回复只作为某次评估 run 的副产物存在 ``test_results.actual_output`` /
``test_results.full_trace.conversation``，绑死在 run 上，既无法在数据集页面预先
生成，也无法版本化复用。本迁移引入四张表把「样例的 agent 回复」提升为独立实体：

- ``agent_reply_versions``    每次生成/编辑产生一条 append-only 版本行，
                              ``version_number`` 是 per-(dataset_type, case_ref)
                              的单调计数（照 evaluator_versions 的范式）。
- ``agent_reply_case_states`` 每个样例一行，``current_version_id`` 指向当前版本。
                              单独成表而非挂在样例表上，因为三类数据集落地方式
                              不对称（多轮对话集的样例真身在 Langfuse，PG 里没有
                              case 表可加列）。
- ``agent_reply_jobs``        批量生成任务，状态落库以支持刷新恢复 / 取消 / 重试。
- ``agent_reply_job_items``   任务的逐样例项，用于单条重试、进度统计，以及
                              「同一样例 + 同一 agent 配置已有在途任务」的去重。

样例引用用 (dataset_type, case_ref) 多态键：candidate / benchmark 存本地表主键的
字符串形式，conversation 存 Langfuse dataset item id —— 因此不建外键。

另给评估侧加两列：``test_runs.reply_source`` 记录该 run 用实时调用还是预生成
回复；``test_results.reply_version_id`` 固定记录实际消费的版本，历史可复现。

幂等：每张表 / 每列都先 inspect 判存在再建。
逐列对照 db_models/tables.py 的 AgentReplyVersionRow / AgentReplyCaseStateRow /
AgentReplyJobRow / AgentReplyJobItemRow 列名。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_INTERNAL_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def _has_column(insp, table: str, column: str) -> bool:
    return column in {c["name"] for c in insp.get_columns(table)}


def _tenant_col() -> sa.Column:
    return sa.Column(
        "tenant_id",
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id"),
        nullable=False,
        server_default=_INTERNAL_TENANT_ID,
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _create_jobs() -> None:
    op.create_table(
        "agent_reply_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # candidate | benchmark | conversation
        sa.Column("dataset_type", sa.String(16), nullable=False),
        sa.Column("dataset_name", sa.String(256), nullable=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # 完整 EvalAgentConfig 快照（本仓库没有 agent 配置表，只能整体快照）
        sa.Column("agent_config", JSONB(), nullable=False),
        # agent_config 归一化后的 sha256，用于「同样例+同配置在途」去重
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        # 用户自定义版本号（agent 配置差异由它体现，不额外建配置版本链）
        sa.Column("version_label", sa.String(64), nullable=True),
        # running | completed | failed | cancelled | interrupted
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("running_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        _tenant_col(),
    )
    op.create_index("ix_agent_reply_jobs_tenant_id", "agent_reply_jobs", ["tenant_id"])
    op.create_index("ix_agent_reply_jobs_status", "agent_reply_jobs", ["status"])
    op.create_index(
        "ix_agent_reply_jobs_fingerprint", "agent_reply_jobs", ["config_fingerprint"]
    )
    op.create_index(
        "ix_agent_reply_jobs_dataset", "agent_reply_jobs", ["dataset_type", "dataset_name"]
    )


def _create_versions() -> None:
    op.create_table(
        "agent_reply_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_type", sa.String(16), nullable=False),
        # candidate/benchmark = 本地表主键字符串；conversation = Langfuse item id。
        # 多态引用，故不建外键。
        sa.Column("case_ref", sa.String(256), nullable=False),
        sa.Column("dataset_name", sa.String(256), nullable=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # per-(dataset_type, case_ref) 单调计数，从 1 开始
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(64), nullable=True),
        # 单轮：agent 回复正文。多轮：build_transcript(turns) 的整段文本。
        sa.Column("content", sa.Text(), nullable=True),
        # 多轮逐轮结构，与 multiturn.replay_conversation 的 turns 同构，
        # 便于评估侧无改动复用 score_conversation / build_transcript。
        sa.Column("turns", JSONB(), nullable=True),
        # SSE steps / tool_calls / usage 等原始轨迹
        sa.Column("raw_trace", JSONB(), nullable=True),
        sa.Column("agent_config", JSONB(), nullable=False),
        sa.Column("config_fingerprint", sa.String(64), nullable=True),
        # pending | running | succeeded | failed
        sa.Column("status", sa.String(16), nullable=False, server_default="succeeded"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("edited", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_reply_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _created_at(),
        _updated_at(),
        _tenant_col(),
        sa.UniqueConstraint(
            "dataset_type",
            "case_ref",
            "version_number",
            name="uq_agent_reply_versions_case_version",
        ),
    )
    op.create_index(
        "ix_agent_reply_versions_tenant_id", "agent_reply_versions", ["tenant_id"]
    )
    op.create_index("ix_agent_reply_versions_job_id", "agent_reply_versions", ["job_id"])
    op.create_index(
        "ix_agent_reply_versions_case",
        "agent_reply_versions",
        ["dataset_type", "case_ref"],
    )


def _create_case_states() -> None:
    op.create_table(
        "agent_reply_case_states",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_type", sa.String(16), nullable=False),
        sa.Column("case_ref", sa.String(256), nullable=False),
        sa.Column("dataset_name", sa.String(256), nullable=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # 当前版本指针；删掉当前版本后由业务代码改指或置 NULL
        sa.Column(
            "current_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_reply_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _created_at(),
        _updated_at(),
        _tenant_col(),
        sa.UniqueConstraint(
            "dataset_type", "case_ref", name="uq_agent_reply_case_states_case"
        ),
    )
    op.create_index(
        "ix_agent_reply_case_states_tenant_id", "agent_reply_case_states", ["tenant_id"]
    )
    op.create_index(
        "ix_agent_reply_case_states_dataset",
        "agent_reply_case_states",
        ["dataset_type", "dataset_name"],
    )


def _create_job_items() -> None:
    op.create_table(
        "agent_reply_job_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_reply_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_ref", sa.String(256), nullable=False),
        # 生成时的问题快照，便于失败项在 UI 上可辨认
        sa.Column("question", sa.Text(), nullable=True),
        # pending | running | succeeded | failed | cancelled
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_reply_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        _tenant_col(),
        sa.UniqueConstraint("job_id", "case_ref", name="uq_agent_reply_job_items_job_case"),
    )
    op.create_index(
        "ix_agent_reply_job_items_tenant_id", "agent_reply_job_items", ["tenant_id"]
    )
    op.create_index("ix_agent_reply_job_items_job_id", "agent_reply_job_items", ["job_id"])
    op.create_index(
        "ix_agent_reply_job_items_case_ref", "agent_reply_job_items", ["case_ref"]
    )
    # 在途去重查询：按 case_ref + status 过滤
    op.create_index(
        "ix_agent_reply_job_items_case_status",
        "agent_reply_job_items",
        ["case_ref", "status"],
    )


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    # 建表顺序受外键约束：jobs → versions → case_states / job_items
    if "agent_reply_jobs" not in tables:
        _create_jobs()
    if "agent_reply_versions" not in tables:
        _create_versions()
    if "agent_reply_case_states" not in tables:
        _create_case_states()
    if "agent_reply_job_items" not in tables:
        _create_job_items()

    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    # 评估侧：run 级数据来源 + result 级实际消费版本
    if "test_runs" in tables and not _has_column(insp, "test_runs", "reply_source"):
        op.add_column(
            "test_runs",
            sa.Column(
                "reply_source",
                sa.String(16),
                nullable=False,
                server_default="live",
            ),
        )
    if "test_results" in tables and not _has_column(insp, "test_results", "reply_version_id"):
        op.add_column(
            "test_results",
            sa.Column(
                "reply_version_id",
                UUID(as_uuid=True),
                sa.ForeignKey("agent_reply_versions.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_test_results_reply_version_id", "test_results", ["reply_version_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "test_results" in tables and _has_column(insp, "test_results", "reply_version_id"):
        op.drop_index("ix_test_results_reply_version_id", table_name="test_results")
        op.drop_column("test_results", "reply_version_id")
    if "test_runs" in tables and _has_column(insp, "test_runs", "reply_source"):
        op.drop_column("test_runs", "reply_source")

    # drop_table 会一并移除其索引/唯一约束，无需逐个 drop。
    # 反序：先删引用方，再删被引用方。
    for name in (
        "agent_reply_job_items",
        "agent_reply_case_states",
        "agent_reply_versions",
        "agent_reply_jobs",
    ):
        if name in tables:
            op.drop_table(name)
