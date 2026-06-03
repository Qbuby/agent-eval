from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_eval.config import settings

engine = create_async_engine(settings.db.async_url, echo=False, pool_size=10, max_overflow=20)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Bring the database schema up to date by running Alembic migrations.

    This used to call ``Base.metadata.create_all``, which built tables directly
    from the ORM and bypassed Alembic entirely. That bypass let the models and
    the migration chain drift apart (see migration 0017's docstring). Now it
    runs ``alembic upgrade head`` so the CLI ``init-db`` command and production
    deploys take the *same* path and the ``alembic_version`` bookkeeping stays
    correct.

    Alembic is synchronous; we run it in a thread so this stays an async API.
    """
    import asyncio
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    # db.py lives at src/agent_eval/db.py → project root is parents[2],
    # which is also /app inside the container image.
    root = Path(__file__).resolve().parents[2]

    def _upgrade() -> None:
        cfg = Config(str(root / "alembic.ini"))
        # script_location in the ini is relative; anchor it to the project
        # root so this works regardless of the process's cwd. env.py builds
        # the sync DB URL from DB_* env vars.
        cfg.set_main_option("script_location", str(root / "alembic"))
        command.upgrade(cfg, "head")

    await asyncio.to_thread(_upgrade)


async def close_db() -> None:
    await engine.dispose()
