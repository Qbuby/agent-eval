"""租户上下文（多租户隔离的全局传递载体）。

为什么用 ContextVar 而不是塞 session.info：项目里 session 有两种来源——
FastAPI 依赖 ``get_db()``，以及大量后台/服务层直连 ``async with
async_session_factory()``（scheduler / warmer / lifespan）。如果只把租户塞进
``get_db`` 那个 session，直连 session 就拿不到。ContextVar 跟随 asyncio 任务
传播，事件监听器（见 db.py）无论从哪种 session 触发都能读到同一个上下文。

默认值 None 的语义很关键：表示「系统 / 后台 / 未鉴权」上下文，监听器据此
**旁路过滤**（superadmin 等效），避免后台查询被租户过滤成空导致崩溃。
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass

# 固定 sentinel：存量数据 / 后台写入归属的「内部租户」。语义即 tenant_id=1。
INTERNAL_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@dataclass(frozen=True)
class TenantContext:
    """当前请求归属的租户。

    superadmin=True 时监听器不注入租户过滤（内部 admin 跨租户可见）。
    """

    tenant_id: uuid.UUID
    superadmin: bool


# 默认 None = 系统/后台/未鉴权上下文 → superadmin 旁路（不过滤）。
_current: ContextVar[TenantContext | None] = ContextVar("current_tenant", default=None)


def set_tenant_context(ctx: TenantContext | None) -> Token:
    """设置当前租户上下文，返回 token 供之后 reset（避免跨请求泄漏）。"""
    return _current.set(ctx)


def reset_tenant_context(token: Token) -> None:
    """用 set 返回的 token 还原上下文。"""
    _current.reset(token)


def get_tenant_context() -> TenantContext | None:
    """读取当前租户上下文；None 表示系统/后台/未鉴权（不过滤）。"""
    return _current.get()
