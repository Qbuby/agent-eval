"""Add multi-tenant isolation + external-customer Portal tables.

Background
----------
1-C 多租户改造（见 .wf_multitenant_design.md §3/§4）。本迁移完成五件事：

1. 建 ``tenants`` 表，并插入固定 sentinel「内部租户」
   (id=00000000-0000-0000-0000-000000000001)。存量数据全部归属它。
2. ``users`` 加 ``tenant_id`` + ``is_superadmin``：先 nullable 加列、backfill
   （tenant_id=sentinel；role='admin' 的置 is_superadmin=true），再收紧 NOT NULL。
3. §3.3 列出的存量核心表统一加 ``tenant_id``：add nullable → backfill sentinel
   → NOT NULL + index + FK。
4. 建三张 Portal 新表：portal_sample_batches / portal_samples /
   sample_feedbacks（均带 tenant_id）。

Idempotency
-----------
参考 0017：用 ``sa_inspect`` 检查表/列/索引是否已存在再操作。dev DB 历史上可能
被 create_all 建过这些对象（虽然 create_all 已移除），保持防御让迁移在干净库
和 dev 库都能干净跑通。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


# sentinel 内部租户 id（与 db_models/tenant_context.INTERNAL_TENANT_ID 一致）。
_INTERNAL_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# §3.3：要加 TenantMixin（tenant_id 列）的存量核心表。
_TENANT_TABLES = [
    "dataset_versions",
    "test_cases",
    "test_runs",
    "test_results",
    "evaluation_scores",
    "optimizations",
    "projects",
    "categories",
    "benchmark_versions",
    "benchmark_cases",
    "candidate_cases",
    "import_batches",
    "dataset_metadata",
    "eval_case_sources",
    "evaluator_configs",
    "evaluator_versions",
    "evaluator_providers",
    "routing_rules",
    "routing_logs",
    "loop_control_log",
    "trace_watch_cursors",
    "audit_logs",
    "example_fingerprints",
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    existing_tables = set(insp.get_table_names())

    def _columns(table: str) -> set[str]:
        if table not in existing_tables:
            return set()
        return {c["name"] for c in insp.get_columns(table)}

    def _indexes(table: str) -> set[str]:
        if table not in existing_tables:
            return set()
        return {ix["name"] for ix in insp.get_indexes(table)}

    def _fks(table: str) -> set[str]:
        if table not in existing_tables:
            return set()
        return {fk["name"] for fk in insp.get_foreign_keys(table) if fk.get("name")}

    # ── 1. tenants 表 + sentinel 内部租户 ────────────────────────────────
    if "tenants" not in existing_tables:
        op.create_table(
            "tenants",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("slug", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16),
                      server_default="active", nullable=False),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True),
                      server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("slug", name="uq_tenants_slug"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"],
                                    name="fk_tenants_created_by_users"),
        )
        existing_tables.add("tenants")

    # 插入 sentinel；ON CONFLICT 保证幂等（重复跑不报错）。
    op.execute(sa.text(
        "INSERT INTO tenants (id, name, slug, status, created_at, updated_at) "
        "VALUES (:id, 'Internal', 'internal', 'active', now(), now()) "
        "ON CONFLICT (id) DO NOTHING"
    ).bindparams(id=_INTERNAL_TENANT_ID))

    # ── 2. users 加 tenant_id + is_superadmin ────────────────────────────
    user_cols = _columns("users")
    if "tenant_id" not in user_cols:
        op.add_column("users",
                      sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.execute(sa.text(
            "UPDATE users SET tenant_id = :tid WHERE tenant_id IS NULL"
        ).bindparams(tid=_INTERNAL_TENANT_ID))
        op.alter_column("users", "tenant_id",
                        existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    if "ix_users_tenant_id" not in _indexes("users"):
        op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    if "fk_users_tenant_id_tenants" not in _fks("users"):
        op.create_foreign_key("fk_users_tenant_id_tenants", "users",
                              "tenants", ["tenant_id"], ["id"])

    if "is_superadmin" not in user_cols:
        op.add_column("users",
                      sa.Column("is_superadmin", sa.Boolean(),
                                server_default=sa.text("false"), nullable=False))
        # 内部 admin 即 superadmin（跨租户可见）。
        op.execute(sa.text("UPDATE users SET is_superadmin = true WHERE role = 'admin'"))

    # ── 3. 存量核心表加 tenant_id（nullable → backfill → NOT NULL+idx+FK）─
    for table in _TENANT_TABLES:
        if table not in existing_tables:
            # 该表在当前库不存在（理论上不会，防御）。跳过。
            continue
        cols = _columns(table)
        if "tenant_id" not in cols:
            op.add_column(table,
                          sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
            op.execute(sa.text(
                f'UPDATE "{table}" SET tenant_id = :tid WHERE tenant_id IS NULL'
            ).bindparams(tid=_INTERNAL_TENANT_ID))
            op.alter_column(table, "tenant_id",
                            existing_type=postgresql.UUID(as_uuid=True), nullable=False)
        idx_name = f"ix_{table}_tenant_id"
        if idx_name not in _indexes(table):
            op.create_index(idx_name, table, ["tenant_id"])
        fk_name = f"fk_{table}_tenant_id_tenants"
        if fk_name not in _fks(table):
            op.create_foreign_key(fk_name, table, "tenants", ["tenant_id"], ["id"])

    # ── 4. Portal 三张新表 ────────────────────────────────────────────────
    if "portal_sample_batches" not in existing_tables:
        op.create_table(
            "portal_sample_batches",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("row_count", sa.Integer(),
                      server_default="0", nullable=False),
            sa.Column("status", sa.String(length=16),
                      server_default="active", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                    name="fk_portal_sample_batches_tenant_id_tenants"),
            sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"],
                                    name="fk_portal_sample_batches_uploaded_by_users"),
        )
        op.create_index("ix_portal_sample_batches_tenant_id",
                        "portal_sample_batches", ["tenant_id"])
        existing_tables.add("portal_sample_batches")

    if "portal_samples" not in existing_tables:
        op.create_table(
            "portal_samples",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("row_index", sa.Integer(), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("answer", sa.Text(), nullable=True),
            sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()),
                      server_default=sa.text("'{}'::jsonb"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                    name="fk_portal_samples_tenant_id_tenants"),
            sa.ForeignKeyConstraint(["batch_id"], ["portal_sample_batches.id"],
                                    ondelete="CASCADE",
                                    name="fk_portal_samples_batch_id_batches"),
        )
        op.create_index("ix_portal_samples_tenant_id", "portal_samples", ["tenant_id"])
        op.create_index("ix_portal_samples_batch_id", "portal_samples", ["batch_id"])
        existing_tables.add("portal_samples")

    if "sample_feedbacks" not in existing_tables:
        op.create_table(
            "sample_feedbacks",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("sample_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("rated_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("overall", sa.Integer(), nullable=True),
            sa.Column("scores", postgresql.JSONB(astext_type=sa.Text()),
                      server_default=sa.text("'{}'::jsonb"), nullable=False),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True),
                      server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                                    name="fk_sample_feedbacks_tenant_id_tenants"),
            sa.ForeignKeyConstraint(["sample_id"], ["portal_samples.id"],
                                    ondelete="CASCADE",
                                    name="fk_sample_feedbacks_sample_id_samples"),
            sa.ForeignKeyConstraint(["rated_by"], ["users.id"],
                                    name="fk_sample_feedbacks_rated_by_users"),
            # 一个用户对一个 sample 只一条反馈。
            sa.UniqueConstraint("sample_id", "rated_by",
                                name="uq_sample_feedback_sample_rater"),
        )
        op.create_index("ix_sample_feedbacks_tenant_id", "sample_feedbacks", ["tenant_id"])
        op.create_index("ix_sample_feedbacks_sample_id", "sample_feedbacks", ["sample_id"])
        existing_tables.add("sample_feedbacks")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    existing_tables = set(insp.get_table_names())

    # ── 反向 4：drop Portal 三表（先 drop 依赖方）────────────────────────
    for table in ("sample_feedbacks", "portal_samples", "portal_sample_batches"):
        if table in existing_tables:
            op.drop_table(table)

    # ── 反向 3：存量核心表去掉 tenant_id（FK/index 随列删除）─────────────
    for table in _TENANT_TABLES:
        if table not in existing_tables:
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if "tenant_id" in cols:
            fk_name = f"fk_{table}_tenant_id_tenants"
            existing_fks = {fk["name"] for fk in insp.get_foreign_keys(table) if fk.get("name")}
            if fk_name in existing_fks:
                op.drop_constraint(fk_name, table, type_="foreignkey")
            idx_name = f"ix_{table}_tenant_id"
            existing_idx = {ix["name"] for ix in insp.get_indexes(table)}
            if idx_name in existing_idx:
                op.drop_index(idx_name, table_name=table)
            op.drop_column(table, "tenant_id")

    # ── 反向 2：users 去掉两列 ───────────────────────────────────────────
    user_cols = {c["name"] for c in insp.get_columns("users")}
    user_fks = {fk["name"] for fk in insp.get_foreign_keys("users") if fk.get("name")}
    user_idx = {ix["name"] for ix in insp.get_indexes("users")}
    if "is_superadmin" in user_cols:
        op.drop_column("users", "is_superadmin")
    if "tenant_id" in user_cols:
        if "fk_users_tenant_id_tenants" in user_fks:
            op.drop_constraint("fk_users_tenant_id_tenants", "users", type_="foreignkey")
        if "ix_users_tenant_id" in user_idx:
            op.drop_index("ix_users_tenant_id", table_name="users")
        op.drop_column("users", "tenant_id")

    # ── 反向 1：drop tenants ─────────────────────────────────────────────
    if "tenants" in existing_tables:
        op.drop_table("tenants")
