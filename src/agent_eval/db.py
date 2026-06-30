from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, with_loader_criteria

from agent_eval.config import settings
from agent_eval.db_models.tables import TenantMixin
from agent_eval.db_models.tenant_context import (
    INTERNAL_TENANT_ID,
    get_tenant_context,
)

engine = create_async_engine(settings.db.async_url, echo=False, pool_size=10, max_overflow=20)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------------------------------------------------------
# 多租户隔离：事件监听器（读过滤 + 写盖章）
# ---------------------------------------------------------------------------
#
# 监听器挂在 SQLAlchemy 同步 Session 类上（AsyncSession 内部委托给它），
# 这样无论是 get_db 依赖还是直连 async_session_factory 的后台任务，
# 都会经过同一套租户逻辑。租户从 ContextVar 读（见 db_models/tenant_context）。


@event.listens_for(Session, "do_orm_execute")
def _tenant_read_filter(orm_execute_state) -> None:
    """读过滤：给继承 TenantMixin 的表自动加 ``tenant_id == ctx.tenant_id``。

    旁路（不注入）的三种情况，缺一不可：
    - 非 SELECT（增删改的过滤交给业务自身，不在此拦）。
    - ctx 为 None：系统/后台/未鉴权（scheduler/warmer/lifespan）。若在这里
      注入过滤，后台查询会被过滤成空集甚至误判，必须放行（superadmin 等效）。
    - ctx.superadmin：内部 admin 跨租户可见。

    租户值通过闭包局部变量 ``tid`` 注入：with_loader_criteria 的 lambda 受
    SQLAlchemy「lambda SQL」缓存约束（按类只编译一次结构），但该系统会**自动追踪
    lambda 的闭包变量**并将其作为 bindparam 在每次执行时重新绑定 —— 这正是官方
    多租户 do_orm_execute recipe 的写法，既不会把某个租户的 id 固化进缓存泄漏给
    别的租户，也不会触发 .params() 对 LoaderCriteriaOption 的克隆（后者不支持
    克隆，会 AttributeError: 'LoaderCriteriaOption' object has no attribute
    '__dict__'）。
    """
    if not orm_execute_state.is_select:
        return
    ctx = get_tenant_context()
    if ctx is None or ctx.superadmin:
        return
    tid = ctx.tenant_id
    orm_execute_state.statement = orm_execute_state.statement.options(
        with_loader_criteria(
            TenantMixin,
            lambda cls: cls.tenant_id == tid,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _tenant_write_stamp(session: Session, flush_context, instances) -> None:
    """写盖章：新行若没写 tenant_id，则补当前租户；无上下文则落内部 sentinel。

    保证 TenantMixin 表永不写出 NULL tenant_id（满足 NOT NULL 约束），
    且后台创建的行归属内部租户。已显式设置 tenant_id 的行保持不动。
    """
    ctx = get_tenant_context()
    fallback = ctx.tenant_id if ctx is not None else INTERNAL_TENANT_ID
    for obj in session.new:
        if isinstance(obj, TenantMixin) and getattr(obj, "tenant_id", None) is None:
            obj.tenant_id = fallback


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
