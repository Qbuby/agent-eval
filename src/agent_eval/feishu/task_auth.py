"""后台（无 HTTP 请求上下文）场景下的租户上下文 + JWT 签发封装。

飞书 bot 的定时评估、run 完成通知订阅都跑在常驻进程里，没有 FastAPI 请求
经过 ``get_current_user`` 建立租户上下文。若直接读写 DB 而不显式建立上下文，
db.py 的事件监听器会按 None（superadmin 旁路）执行——要么跨租户泄漏、要么
写出 NULL tenant_id。因此这里提供两个工具：

- ``tenant_scope(tenant_id, superadmin)``：上下文管理器，进入时 set、退出时
  **务必** reset（try/finally），避免 ContextVar 泄漏到同一 asyncio 任务后续
  的其它逻辑（参 tenant_context 模块文档与 langfuse_metrics 的显式做法）。
- ``sign_token_for_user(user_row)``：给「代表某用户跑」的后台任务签一枚短期
  JWT，让它经本地 HTTP 自调时与该用户走同一套权限/租户边界（复用
  bot_service 里已验证的 create_access_token 调用）。
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from agent_eval.auth.security import create_access_token
from agent_eval.db_models.tenant_context import (
    TenantContext,
    reset_tenant_context,
    set_tenant_context,
)


@contextmanager
def tenant_scope(
    tenant_id: uuid.UUID | None, *, superadmin: bool = False
) -> Iterator[None]:
    """在后台任务里临时建立租户上下文，退出时还原。

    tenant_id=None 时进入 superadmin 旁路（不过滤）——仅用于确无租户归属的
    纯系统操作；代表具体用户/租户跑时务必传该用户的 tenant_id。
    """
    ctx = None if tenant_id is None else TenantContext(
        tenant_id=tenant_id, superadmin=superadmin
    )
    token = set_tenant_context(ctx)
    try:
        yield
    finally:
        reset_tenant_context(token)


def sign_token_for_user(user: Any) -> str:
    """给「代表该用户」的后台任务签一枚短期 access JWT。

    user 需有 id / role / tenant_id 属性（UserRow）。token 带 tenant_id claim，
    使后台任务经本地 HTTP 自调时恢复出与该用户一致的租户/权限上下文。
    """
    return create_access_token(
        user.id, user.role, getattr(user, "tenant_id", None)
    )
