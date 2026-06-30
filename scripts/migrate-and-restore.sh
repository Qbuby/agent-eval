#!/bin/sh
# Migrate-and-restore entrypoint for the `migrate` one-shot service.
#
# Goal: bring an arbitrary target database to the latest schema AND, on a
# brand-new (empty) deployment, seed it with the captured production data —
# WITHOUT ever clobbering a database that already holds data.
#
# Flow:
#   1. Wait for Postgres to accept connections (compose already gates on
#      healthcheck, but we double-check so a direct `docker run` is safe too).
#   2. Empty-DB gate: probe whether the `users` table exists and holds >0 rows.
#        - empty  (table missing OR zero rows) → restore deploy/seed/seed.sql
#        - non-empty                            → SKIP restore (never clobber)
#   3. Always run `alembic upgrade head`:
#        - after a seed restore, the dump already carries alembic_version=0020,
#          so this is a no-op unless newer revisions exist (then it catches up)
#        - on a non-empty DB, this is the normal migrate behaviour
#
# The gate keys on `users` because every real deployment has users; the empty
# schema created by a fresh `alembic upgrade` would also have the table but
# zero rows, so "table missing OR zero rows" == "safe to seed".
#
# Env (same as the rest of the stack):
#   DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME
#   SEED_FILE         — path to the seed dump (default /app/deploy/seed/seed.sql)
#   SEED_DISABLE      — set to "1" to force-skip restore (schema-only deploy)
set -e

DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-agent_eval}"
SEED_FILE="${SEED_FILE:-/app/deploy/seed/seed.sql}"

export PGPASSWORD="${DB_PASSWORD:-postgres}"
PSQL="psql -v ON_ERROR_STOP=1 -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME"

echo "[migrate] waiting for postgres at $DB_HOST:$DB_PORT ..."
i=0
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
        echo "[migrate] postgres not ready after 60 tries; aborting" >&2
        exit 1
    fi
    sleep 1
done
echo "[migrate] postgres is accepting connections"

# --- Empty-DB gate -------------------------------------------------------
# Returns the users row count, or 0 if the table doesn't exist yet.
# to_regclass() is NULL when the relation is absent → coalesce to a 0 count.
row_count=$(
    $PSQL -tAc "
        SELECT CASE
                 WHEN to_regclass('public.users') IS NULL THEN 0
                 ELSE (SELECT count(*) FROM public.users)
               END;
    " 2>/dev/null | tr -d '[:space:]'
)
row_count="${row_count:-0}"
echo "[migrate] users row count: $row_count"

if [ "$SEED_DISABLE" = "1" ]; then
    echo "[migrate] SEED_DISABLE=1 → skipping seed restore (schema-only)"
elif [ "$row_count" = "0" ]; then
    if [ -r "$SEED_FILE" ]; then
        echo "[migrate] empty database → restoring seed from $SEED_FILE"
        $PSQL -f "$SEED_FILE"
        echo "[migrate] seed restore complete"
    else
        echo "[migrate] empty database but no seed file at $SEED_FILE → schema-only via alembic"
    fi
else
    echo "[migrate] database already has data (users=$row_count) → SKIP seed restore (never clobber)"
fi

# --- Always converge schema to head -------------------------------------
# After a seed restore this is usually a no-op (dump carries alembic_version);
# it only does work when newer revisions exist than the seed snapshot.
echo "[migrate] running alembic upgrade head"
alembic upgrade head
echo "[migrate] done"
