from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.auth.security import decode_access_token
from agent_eval.config import settings
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import UserRow
from agent_eval.db_models.tenant_context import (
    TenantContext,
    reset_tenant_context,
    set_tenant_context,
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

# Role constants. Currently the UserRow.role column only carries "admin"/"user".
# ROLE_EXTERNAL is reserved for a future external-customer tier and is NOT wired
# into any endpoint yet.
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_EXTERNAL = "external_customer"


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> AsyncGenerator[UserRow | None, None]:
    """解析当前用户，并把租户上下文写进 ContextVar 供事件监听器使用。

    设计成 yield 依赖（而非普通 return）是为了在请求结束时 reset ContextVar：
    FastAPI 会在响应发出后执行 yield 之后的代码。这样租户上下文严格随请求
    生命周期，不会泄漏到复用同一 worker 任务的下一个请求，也无需改 app.py
    加中间件。set 返回的 token 用来精确还原到设置前的值。

    auth 关闭时返回 None 且**不设**上下文 —— 监听器据此旁路过滤，保持
    dev「关 auth = 看全部」的行为。
    """
    if not settings.auth.enabled:
        yield None
        return

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = uuid.UUID(payload["sub"])
    result = await db.execute(select(UserRow).where(UserRow.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    # 设置租户上下文：监听器读它做读过滤 / 写盖章。superadmin（内部 admin）
    # 跨租户可见，靠监听器对 superadmin 旁路实现。
    ctx_token = set_tenant_context(TenantContext(user.tenant_id, user.is_superadmin))
    try:
        yield user
    finally:
        reset_tenant_context(ctx_token)


async def require_admin(user: UserRow | None = Depends(get_current_user)) -> UserRow:
    # auth 关闭（dev 模式）旁路：get_current_user 已返回 None，这里若继续走
    # 会因 user is None 抛 401，破坏「关 auth = 全放行」的 dev 行为。统一在取
    # user.role 之前放行，与 get_current_user 的旁路保持一致。
    if not settings.auth.enabled:
        return None
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def require_role(*allowed_roles: str) -> Callable[[UserRow | None], Awaitable[UserRow]]:
    """Build a dependency that requires an authenticated user whose role is one
    of ``allowed_roles``.

    Mirrors ``require_admin`` semantics: when ``settings.auth.enabled`` is False
    (dev 模式) the dependency passes through (returns None), consistent with
    ``get_current_user`` 的旁路. When auth is enabled but the user is missing we
    raise 401, and when the user is authenticated but their role is not allowed
    we raise 403.

    Usage::

        from agent_eval.auth.dependencies import require_role, ROLE_ADMIN

        @router.post("/scheduler/pause", dependencies=[Depends(require_role(ROLE_ADMIN))])
        async def pause(...):
            ...

        # or to read the user:
        async def handler(user: UserRow = Depends(require_role(ROLE_ADMIN, ROLE_USER))):
            ...
    """

    async def _require_role(user: UserRow | None = Depends(get_current_user)) -> UserRow:
        # auth 关闭（dev 模式）旁路：get_current_user 已返回 None，必须在取
        # user.role 之前放行，否则下面 user is None 会抛 401，破坏 dev 模式。
        # 与 require_admin / get_current_user 的旁路保持一致。
        if not settings.auth.enabled:
            return None
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role",
            )
        return user

    return _require_role


def require_internal() -> Callable[[UserRow | None], Awaitable[UserRow]]:
    """内部角色门禁（admin | user）的便捷依赖工厂。

    返回 ``require_role(ROLE_ADMIN, ROLE_USER)`` 生成的依赖函数：内部超管
    （admin）和内部普通用户（user）放行，**external_customer 会拿到 403**。
    用于把各内部 router 的 router 级 ``dependencies`` 从「只验登录」收紧为
    「内部角色」，堵住外部客户带 token GET 内部 API 的越权漏洞。

    语义与 ``require_role`` 完全一致：auth 关闭（dev 模式）时统一放行
    （内层依赖在取 user.role 之前返回 None），不破坏「关 auth = 全放行」。

    用法（注意是工厂，需调用一次拿到依赖函数）::

        from agent_eval.auth.dependencies import require_internal

        router = APIRouter(dependencies=[Depends(require_internal())])
    """
    return require_role(ROLE_ADMIN, ROLE_USER)


# Portal 写操作的便捷依赖：外部客户 + 内部 admin 都放行（admin 便于测试/代操作）。
# 等价于 require_role(ROLE_EXTERNAL, ROLE_ADMIN)，给 portal 路由复用。
require_external = require_role(ROLE_EXTERNAL, ROLE_ADMIN)
