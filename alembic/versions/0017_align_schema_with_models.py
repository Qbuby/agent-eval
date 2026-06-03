"""Align DB schema with ORM models (drift accumulated under create_all bypass).

Background
----------
``db.py`` historically exposed ``init_db()`` → ``Base.metadata.create_all``,
a path that creates tables straight from the ORM without going through
Alembic. Over time the models drifted from the migration chain (which had
stopped at 0016). ``alembic check`` against the models surfaced three classes
of drift; this migration reconciles all of them so a clean ``alembic upgrade
head`` reproduces exactly the schema the code expects.

1. **Missing tables** — ``audit_logs`` and ``example_fingerprints`` are
   defined as models (used by ``governance/audit.py`` and dataset-dedup
   fingerprinting) but were never created by any migration. On a fresh
   production DB these would not exist and those features would raise
   ``relation does not exist``.

2. **Nullable → NOT NULL** — a batch of ``created_at`` / ``updated_at`` /
   ``tags`` columns are ``nullable=False`` in the models but ``NULL``-able in
   the DB. These columns use a Python-side ``default`` (NOT a
   ``server_default``), so existing rows may legitimately hold NULL. We
   backfill NULLs before tightening the constraint, otherwise ALTER would
   fail on legacy data.

3. **Stale indexes** — six indexes still exist in the DB but the models no
   longer declare ``index=True`` for those columns. Dropped to match.

Idempotency
-----------
A DB bootstrapped via the old ``create_all`` path may already contain the two
tables / their indexes, while a fresh production DB will not. We therefore
inspect the live schema and only create what is missing — the migration runs
cleanly on both. The companion code change removes the ``create_all`` bypass
from ``init_db`` so schema can only evolve through migrations from here on.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


_NOT_NULL_TIMESTAMPS = [
    ("benchmark_cases", "created_at"),
    ("benchmark_cases", "updated_at"),
    ("benchmark_versions", "created_at"),
    ("candidate_cases", "created_at"),
    ("candidate_cases", "updated_at"),
    ("categories", "created_at"),
    ("import_batches", "created_at"),
    ("projects", "created_at"),
    ("projects", "updated_at"),
    ("system_configs", "created_at"),
    ("system_configs", "updated_at"),
]
_NOT_NULL_TAGS = [
    ("benchmark_cases", "tags"),
    ("candidate_cases", "tags"),
    ("test_cases", "tags"),
]
_STALE_INDEXES = [
    ("ix_evaluator_providers_provider_type", "evaluator_providers"),
    ("ix_routing_logs_created_at", "routing_logs"),
    ("ix_routing_logs_status", "routing_logs"),
    ("ix_routing_rules_priority", "routing_rules"),
    ("ix_routing_rules_source_project", "routing_rules"),
    ("ix_trace_watch_cursors_status", "trace_watch_cursors"),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    existing_tables = set(insp.get_table_names())

    def _indexes(table: str) -> set[str]:
        if table not in existing_tables:
            return set()
        return {ix["name"] for ix in insp.get_indexes(table)}

    def _columns(table: str) -> set[str]:
        if table not in existing_tables:
            return set()
        return {c["name"] for c in insp.get_columns(table)}

    # ── 1. Missing tables (skip if a create_all DB already has them) ─────
    if "audit_logs" not in existing_tables:
        op.create_table(
            "audit_logs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("entity_type", sa.String(length=32), nullable=False),
            sa.Column("entity_id", sa.String(length=256), nullable=False),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        )
    audit_idx = _indexes("audit_logs")
    for col in ("entity_type", "entity_id", "action", "user_id", "created_at"):
        name = f"ix_audit_logs_{col}"
        if name not in audit_idx:
            op.create_index(name, "audit_logs", [col])

    if "example_fingerprints" not in existing_tables:
        op.create_table(
            "example_fingerprints",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("dataset_name", sa.String(length=256), nullable=False),
            sa.Column("example_id", sa.String(length=256), nullable=False),
            sa.Column("fingerprint", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("dataset_name", "fingerprint", name="uq_dataset_fingerprint"),
        )
    ef_idx = _indexes("example_fingerprints")
    for col in ("dataset_name", "fingerprint"):
        name = f"ix_example_fingerprints_{col}"
        if name not in ef_idx:
            op.create_index(name, "example_fingerprints", [col])

    # ── 2. Nullable → NOT NULL (backfill first, then tighten) ────────────
    for table, col in _NOT_NULL_TIMESTAMPS:
        op.execute(sa.text(f'UPDATE "{table}" SET "{col}" = now() WHERE "{col}" IS NULL'))
        op.alter_column(table, col, existing_type=sa.DateTime(timezone=True), nullable=False)
    for table, col in _NOT_NULL_TAGS:
        op.execute(sa.text(f"UPDATE \"{table}\" SET \"{col}\" = '{{}}' WHERE \"{col}\" IS NULL"))
        op.alter_column(table, col, existing_type=postgresql.ARRAY(sa.Text()), nullable=False)

    # ── 3. Drop stale indexes ────────────────────────────────────────────
    for idx, table in _STALE_INDEXES:
        if idx in _indexes(table):
            op.drop_index(idx, table_name=table)

    # ── 4. Columns/types added to models post-0016 but never migrated ────
    # These were created in the dev DB by the create_all bypass, so they were
    # invisible to "model vs dev-DB" checks — only a clean-DB migration run
    # surfaced them. Each is guarded so this migration is also a no-op on a
    # dev DB that already has the column.
    eval_cfg_cols = _columns("evaluator_configs")
    if "evaluator_configs" in existing_tables and "tag" not in eval_cfg_cols:
        # NOT NULL with no historical source → add nullable, backfill from
        # name (the model default semantics), then tighten.
        op.add_column("evaluator_configs",
                      sa.Column("tag", sa.String(length=128), nullable=True))
        op.execute(sa.text(
            'UPDATE evaluator_configs SET tag = name WHERE tag IS NULL'
        ))
        op.alter_column("evaluator_configs", "tag",
                        existing_type=sa.String(length=128), nullable=False)
    # evaluator_type was NOT NULL in the chain but is Optional in the model.
    if "evaluator_configs" in existing_tables:
        op.alter_column("evaluator_configs", "evaluator_type",
                        existing_type=sa.String(length=32), nullable=True)

    tr_cols = _columns("test_results")
    if "test_results" in existing_tables and "cache_creation_tokens" not in tr_cols:
        op.add_column("test_results",
                      sa.Column("cache_creation_tokens", sa.Integer(), nullable=True))
    if "test_results" in existing_tables and "cache_read_tokens" not in tr_cols:
        op.add_column("test_results",
                      sa.Column("cache_read_tokens", sa.Integer(), nullable=True))
    if "test_results" in existing_tables:
        op.alter_column("test_results", "status",
                        existing_type=sa.String(length=16),
                        type_=sa.String(length=32),
                        existing_nullable=False)

    trun_cols = _columns("test_runs")
    if "test_runs" in existing_tables and "deleted_at" not in trun_cols:
        op.add_column("test_runs",
                      sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # Reverse the post-0016 column/type fixes.
    op.drop_column("test_runs", "deleted_at")
    op.alter_column("test_results", "status",
                    existing_type=sa.String(length=32),
                    type_=sa.String(length=16),
                    existing_nullable=False)
    op.drop_column("test_results", "cache_read_tokens")
    op.drop_column("test_results", "cache_creation_tokens")
    op.alter_column("evaluator_configs", "evaluator_type",
                    existing_type=sa.String(length=32), nullable=False)
    op.drop_column("evaluator_configs", "tag")

    op.create_index("ix_trace_watch_cursors_status", "trace_watch_cursors", ["status"])
    op.create_index("ix_routing_rules_source_project", "routing_rules", ["source_project"])
    op.create_index("ix_routing_rules_priority", "routing_rules", ["priority"])
    op.create_index("ix_routing_logs_status", "routing_logs", ["status"])
    op.create_index("ix_routing_logs_created_at", "routing_logs", ["created_at"])
    op.create_index("ix_evaluator_providers_provider_type", "evaluator_providers", ["provider_type"])

    for table, col in _NOT_NULL_TAGS:
        op.alter_column(table, col, existing_type=postgresql.ARRAY(sa.Text()), nullable=True)
    for table, col in _NOT_NULL_TIMESTAMPS:
        op.alter_column(table, col, existing_type=sa.DateTime(timezone=True), nullable=True)

    op.drop_table("example_fingerprints")
    op.drop_table("audit_logs")
