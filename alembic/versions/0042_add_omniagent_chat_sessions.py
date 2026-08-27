"""add OmniAgent chat sessions + messages (系统智能体多会话对话)

Revision ID: 0042
Revises: 0041
Create Date: 2026-08-24

Background
----------
OmniAgent 对话页此前只有前端与 SSE 代理，没有落库：刷新即失去上下文，无法回看
之前问过什么，也没法把「聊出来的好问题」沉淀成样例。本迁移建两张表：

- ``omniagent_chat_sessions``  用户私有会话目录。``thread_id`` 是发给 OmniAgent
  的稳定上下文键（同一会话每轮复用它，agent 侧才有多轮记忆）；软删除只隐藏
  目录，不删上游 checkpoint。``active_message_id`` 充当同会话单飞门禁：非空即
  表示该会话有一条 assistant 消息正在流，第二个请求直接 409。
- ``omniagent_chat_messages``  会话内的消息，``(session_id, sequence)`` 唯一。
  ``content`` 用 JSONB 而非 Text：DB 层允许存 JSON 字符串（前端契约仍是纯字符
  串，由 router 投影），将来若要存多模态 content blocks 不必再迁移。
  ``retry_of_message_id`` 让「重新生成」成为一条新 attempt 行而非原地覆盖，
  历史尝试可追。

两表都挂 TenantMixin（tenant_id NOT NULL，default 内部 sentinel），与其它客户
可分离表对齐；用户私有性由 ``created_by`` 在 router 层过滤，租户隔离由 db.py
的监听器负责。

幂等：表已存在则跳过。逐列对照 db_models/tables.py 的
OmniAgentChatSessionRow / OmniAgentChatMessageRow。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

_INTERNAL_TENANT_ID = "00000000-0000-0000-0000-000000000001"


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


def _create_sessions() -> None:
    op.create_table(
        "omniagent_chat_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # 会话归属人。用户删号连带删会话（个人数据，不做保留）。
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # 发给 OmniAgent 的 configurable.thread_id，全局唯一。
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default="新对话"),
        # auto = 由首条 user 消息摘要而来，后续仍可被自动覆盖；manual = 用户改过名，
        # 自动摘要不再动它。
        sa.Column("title_source", sa.String(16), nullable=False, server_default="auto"),
        # 非空 = 该会话有消息正在流（同会话单飞门禁）。进程重启后残留由 router
        # 侧的 stale 判定清理，不设外键（指向的消息可能正在同一事务里创建）。
        sa.Column("active_message_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        _tenant_col(),
        sa.UniqueConstraint("thread_id", name="uq_omniagent_sessions_thread_id"),
    )
    op.create_index(
        "ix_omniagent_chat_sessions_tenant_id", "omniagent_chat_sessions", ["tenant_id"]
    )
    op.create_index(
        "ix_omniagent_chat_sessions_created_by",
        "omniagent_chat_sessions",
        ["created_by"],
    )
    # 列表页的唯一查询形状：本租户 + 本人 + 未删除，按 last_message_at 倒序。
    op.create_index(
        "ix_omniagent_sessions_owner_recent",
        "omniagent_chat_sessions",
        ["tenant_id", "created_by", "deleted_at", "last_message_at"],
    )


def _create_messages() -> None:
    op.create_table(
        "omniagent_chat_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # per-session 单调计数，从 1 开始。分页游标就是它（before_sequence）。
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),  # user | assistant
        # JSONB 而非 Text：当前只存 JSON 字符串（前端契约是纯字符串），
        # 留出将来存多模态 content blocks 的余地而无需再迁移。
        sa.Column("content", JSONB(), nullable=False),
        # completed | streaming | failed | cancelled
        sa.Column("status", sa.String(16), nullable=False, server_default="completed"),
        # [{id, name, input, output, error, duration_ms}]，随 tool_start/tool_end 成形。
        sa.Column("tool_calls", JSONB(), nullable=True),
        sa.Column("structured_output", JSONB(), nullable=True),
        # 保留字段名 metadata（ORM 侧映射为 message_metadata，避开 Declarative 保留名）。
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        # 「重新生成」产生的新 attempt 指回被重跑的那条，原行保留。
        sa.Column(
            "retry_of_message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("omniagent_chat_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _created_at(),
        _updated_at(),
        _tenant_col(),
        sa.UniqueConstraint("session_id", "sequence", name="uq_omniagent_message_sequence"),
    )
    op.create_index(
        "ix_omniagent_chat_messages_tenant_id", "omniagent_chat_messages", ["tenant_id"]
    )
    op.create_index(
        "ix_omniagent_chat_messages_session_id",
        "omniagent_chat_messages",
        ["session_id"],
    )


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    # 建表顺序受外键约束：sessions → messages。
    if "omniagent_chat_sessions" not in tables:
        _create_sessions()
    if "omniagent_chat_messages" not in tables:
        _create_messages()


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    # drop_table 会一并移除索引/唯一约束。反序：先删引用方。
    for name in ("omniagent_chat_messages", "omniagent_chat_sessions"):
        if name in tables:
            op.drop_table(name)
