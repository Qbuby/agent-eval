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

    # 多副本并发收敛 schema（backend 每个实例启动都跑 alembic upgrade head）时，
    # 用 Postgres 会话级咨询锁串行化：同一时刻只有一个连接真正迁移，其余阻塞等它
    # 释放后再跑（届时已是 no-op）。
    #
    # 锁必须放在**独立连接**上，绝不能在 alembic 的迁移连接上执行 SQL —— 在迁移
    # 连接上 exec_driver_sql 会触发 SQLAlchemy autobegin 一个外层事务，干扰 alembic
    # 自己的事务管理，导致迁移 DDL 跑了但 alembic_version 更新被回滚（版本号停在
    # 升级前）。所以这里单开一个锁连接，与迁移连接完全隔离。
    lock_conn = connectable.connect()
    lock_conn.exec_driver_sql("SELECT pg_advisory_lock(72181)")
    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        try:
            lock_conn.exec_driver_sql("SELECT pg_advisory_unlock(72181)")
        finally:
            lock_conn.close()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
