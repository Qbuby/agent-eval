"""Apply 0009 DDL idempotently (DB has no alembic_version table)."""
import psycopg2

conn = psycopg2.connect(host="localhost", user="postgres", password="612375", dbname="agent_eval")
cur = conn.cursor()


def col_exists(table: str, col: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
        (table, col),
    )
    return cur.fetchone() is not None


def table_exists(name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name=%s",
        (name,),
    )
    return cur.fetchone() is not None


def run(label: str, stmt: str) -> None:
    try:
        cur.execute(stmt)
        conn.commit()
        print(f"OK: {label}")
    except Exception as e:
        conn.rollback()
        print(f"ERR: {label}: {e}")
        raise


# eval_case_sources
if not table_exists("eval_case_sources"):
    run("create eval_case_sources", """
        CREATE TABLE eval_case_sources (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            source_kind VARCHAR(32) NOT NULL,
            file_format VARCHAR(16),
            cases JSONB NOT NULL,
            created_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

# evaluator_configs
if not table_exists("evaluator_configs"):
    run("create evaluator_configs", """
        CREATE TABLE evaluator_configs (
            id UUID PRIMARY KEY,
            name VARCHAR(128) NOT NULL UNIQUE,
            evaluator_type VARCHAR(32) NOT NULL,
            description TEXT,
            params JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

# test_runs columns
if not col_exists("test_runs", "eval_case_source_id"):
    run("test_runs.eval_case_source_id col",
        "ALTER TABLE test_runs ADD COLUMN eval_case_source_id UUID")
    run("test_runs.eval_case_source_id fk", """
        ALTER TABLE test_runs
        ADD CONSTRAINT test_runs_eval_case_source_id_fkey
        FOREIGN KEY (eval_case_source_id) REFERENCES eval_case_sources(id) ON DELETE SET NULL
    """)
    run("test_runs.eval_case_source_id idx",
        "CREATE INDEX ix_test_runs_eval_case_source_id ON test_runs(eval_case_source_id)")

if not col_exists("test_runs", "langsmith_project"):
    run("test_runs.langsmith_project",
        "ALTER TABLE test_runs ADD COLUMN langsmith_project TEXT")

if not col_exists("test_runs", "eval_started_at"):
    run("test_runs.eval_started_at",
        "ALTER TABLE test_runs ADD COLUMN eval_started_at TIMESTAMPTZ")

# test_results columns
for col, ddl in [
    ("question", "ALTER TABLE test_results ADD COLUMN question TEXT"),
    ("thread_id", "ALTER TABLE test_results ADD COLUMN thread_id TEXT"),
    ("langsmith_run_id", "ALTER TABLE test_results ADD COLUMN langsmith_run_id TEXT"),
]:
    if not col_exists("test_results", col):
        run(f"test_results.{col}", ddl)

# Verify
print("\n=== verify ===")
for tbl in ("eval_case_sources", "evaluator_configs"):
    print(f"  table {tbl}: {'OK' if table_exists(tbl) else 'MISSING'}")
for tbl, cols in [
    ("test_runs", ["eval_case_source_id", "langsmith_project", "eval_started_at"]),
    ("test_results", ["question", "thread_id", "langsmith_run_id"]),
]:
    for c in cols:
        ok = col_exists(tbl, c)
        print(f"  {tbl}.{c}: {'OK' if ok else 'MISSING'}")

conn.close()
