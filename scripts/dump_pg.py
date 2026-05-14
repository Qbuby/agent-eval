"""Dump PostgreSQL database to a plain SQL file using asyncpg.

Mimics `pg_dump --data-only --column-inserts` plus schema DDL rebuilt from
pg_catalog. Output file can be replayed with `psql -f ...` after creating
an empty database.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import asyncpg


async def fetch_tables(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename NOT LIKE 'alembic_%'
        ORDER BY tablename
        """
    )
    return [r["tablename"] for r in rows]


async def fetch_alembic_tables(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public' AND tablename LIKE 'alembic_%'
        ORDER BY tablename
        """
    )
    return [r["tablename"] for r in rows]


async def get_table_ddl(conn: asyncpg.Connection, table: str) -> str:
    cols = await conn.fetch(
        """
        SELECT column_name, data_type, udt_name, character_maximum_length,
               numeric_precision, numeric_scale,
               is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=$1
        ORDER BY ordinal_position
        """,
        table,
    )
    parts: list[str] = []
    for c in cols:
        t = c["udt_name"]
        dt = c["data_type"]
        if dt == "ARRAY":
            col_type = f"{t.lstrip('_')}[]"
        elif dt == "USER-DEFINED":
            col_type = t
        elif t == "varchar" and c["character_maximum_length"]:
            col_type = f"varchar({c['character_maximum_length']})"
        elif t == "numeric" and c["numeric_precision"]:
            col_type = f"numeric({c['numeric_precision']},{c['numeric_scale'] or 0})"
        else:
            col_type = dt
        piece = f'    "{c["column_name"]}" {col_type}'
        if c["is_nullable"] == "NO":
            piece += " NOT NULL"
        if c["column_default"]:
            piece += f" DEFAULT {c['column_default']}"
        parts.append(piece)

    pk = await conn.fetch(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = $1::regclass AND i.indisprimary
        ORDER BY array_position(i.indkey, a.attnum)
        """,
        f'public."{table}"',
    )
    if pk:
        pk_cols = ", ".join(f'"{r["attname"]}"' for r in pk)
        parts.append(f"    PRIMARY KEY ({pk_cols})")

    return f'CREATE TABLE "{table}" (\n' + ",\n".join(parts) + "\n);"


async def get_enums(conn: asyncpg.Connection) -> list[tuple[str, list[str]]]:
    rows = await conn.fetch(
        """
        SELECT t.typname, e.enumlabel
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public'
        ORDER BY t.typname, e.enumsortorder
        """
    )
    enums: dict[str, list[str]] = {}
    for r in rows:
        enums.setdefault(r["typname"], []).append(r["enumlabel"])
    return list(enums.items())


async def get_indexes(conn: asyncpg.Connection, table: str) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT indexdef FROM pg_indexes
        WHERE schemaname='public' AND tablename=$1
          AND indexname NOT IN (
              SELECT conname FROM pg_constraint WHERE conrelid = $2::regclass
          )
        """,
        table,
        f'public."{table}"',
    )
    return [r["indexdef"] + ";" for r in rows]


async def get_foreign_keys(conn: asyncpg.Connection, table: str) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT conname, pg_get_constraintdef(oid) AS def
        FROM pg_constraint
        WHERE conrelid = $1::regclass AND contype = 'f'
        """,
        f'public."{table}"',
    )
    return [f'ALTER TABLE "{table}" ADD CONSTRAINT "{r["conname"]}" {r["def"]};' for r in rows]


def format_value(val) -> str:
    import datetime
    import json
    import decimal
    import uuid

    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float, decimal.Decimal)):
        return str(val)
    if isinstance(val, (datetime.datetime, datetime.date, datetime.time)):
        return "'" + val.isoformat() + "'"
    if isinstance(val, uuid.UUID):
        return "'" + str(val) + "'"
    if isinstance(val, (dict, list)):
        return "'" + json.dumps(val, ensure_ascii=False).replace("'", "''") + "'"
    if isinstance(val, bytes):
        return "'\\x" + val.hex() + "'"
    s = str(val).replace("'", "''")
    return "'" + s + "'"


async def dump_table_data(conn: asyncpg.Connection, table: str, out) -> int:
    cols_rec = await conn.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name=$1
        ORDER BY ordinal_position
        """,
        table,
    )
    col_names = [c["column_name"] for c in cols_rec]
    col_list = ", ".join(f'"{c}"' for c in col_names)

    rows = await conn.fetch(f'SELECT {col_list} FROM "{table}"')
    if not rows:
        return 0

    out.write(f"\n-- Data for table: {table} ({len(rows)} rows)\n")
    for row in rows:
        vals = ", ".join(format_value(row[c]) for c in col_names)
        out.write(f'INSERT INTO "{table}" ({col_list}) VALUES ({vals});\n')
    return len(rows)


async def main(out_path: Path) -> None:
    import os
    from dotenv import dotenv_values

    env = dotenv_values(Path(__file__).parent.parent / ".env")
    conn = await asyncpg.connect(
        host=env.get("DB_HOST", "localhost"),
        port=int(env.get("DB_PORT", 5432)),
        user=env.get("DB_USER", "postgres"),
        password=env.get("DB_PASSWORD", ""),
        database=env.get("DB_NAME", "agent_eval"),
    )

    with out_path.open("w", encoding="utf-8") as out:
        out.write("-- Agent Eval PostgreSQL Dump\n")
        out.write(f"-- Database: {env.get('DB_NAME')}\n")
        out.write("-- Generated by scripts/dump_pg.py\n\n")
        out.write("SET client_encoding = 'UTF8';\n")
        out.write("SET standard_conforming_strings = on;\n\n")

        enums = await get_enums(conn)
        if enums:
            out.write("-- Enums\n")
            for name, labels in enums:
                labs = ", ".join(f"'{l}'" for l in labels)
                out.write(f'CREATE TYPE "{name}" AS ENUM ({labs});\n')
            out.write("\n")

        tables = await fetch_tables(conn)
        alembic_tables = await fetch_alembic_tables(conn)
        all_tables = tables + alembic_tables

        print(f"Found {len(all_tables)} tables")

        out.write("-- Schema\n")
        for t in all_tables:
            ddl = await get_table_ddl(conn, t)
            out.write(ddl + "\n\n")

        out.write("-- Data\n")
        total = 0
        for t in all_tables:
            n = await dump_table_data(conn, t, out)
            total += n
            print(f"  {t}: {n} rows")

        out.write("\n-- Indexes\n")
        for t in all_tables:
            for idx in await get_indexes(conn, t):
                out.write(idx + "\n")

        out.write("\n-- Foreign Keys\n")
        for t in all_tables:
            for fk in await get_foreign_keys(conn, t):
                out.write(fk + "\n")

        print(f"\nTotal rows: {total}")
        print(f"Output: {out_path}")

    await conn.close()


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("D:/files/agent-eval-data.sql")
    out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(out))
