import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, engine_from_config

from agent_eval.db_models.tables import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _build_sync_url() -> str:
    """构造 alembic 用的同步 PG URL。

    优先级：DB_HOST/DB_PORT/... 环境变量 > alembic.ini 里的 sqlalchemy.url。
    强制把 asyncpg 驱动替换成 psycopg2 —— alembic 是同步框架，
    用 asyncpg 会报 MissingGreenlet。
    """
    host = os.getenv("DB_HOST")
    if host:
        port = os.getenv("DB_PORT", "5432")
        user = os.getenv("DB_USER", "postgres")
        password = os.getenv("DB_PASSWORD", "postgres")
        name = os.getenv("DB_NAME", "agent_eval")
        return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"

    url = config.get_main_option("sqlalchemy.url") or ""
    return url.replace("+asyncpg", "+psycopg2")


def run_migrations_offline() -> None:
    context.configure(
        url=_build_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = _build_sync_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
