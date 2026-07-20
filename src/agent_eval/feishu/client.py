"""共享的 lark-oapi app-client（app 身份 / tenant_access_token）。

原来 service.py 在 start() 里内联构建 `lark.Client`，只有长连接服务持有。
但 Bitable 读写（功能1）、主动推送（功能2/3）都需要这个 app-client，且它们
可能在后台任务里被触发（此时长连接服务未必已 start，或想解耦）。故把构建逻辑
抽成模块级懒构造单例：

- `get_lark_client()`：返回共享 `lark.Client`（app_id/app_secret），未配置或
  lark-oapi 未安装时返回 None（调用方须判空并给出可读错误，不静默失败）。
- app 身份（tenant_access_token）由 SDK 内部用 app_id/app_secret 自动换取，
  适用于「以应用身份」访问的 API（发消息、被授权为协作者的多维表格）。
- 访问**用户私人**多维表格需 user_access_token（见 feishu/oauth.py），不走这里。

单例用 lru_cache 缓存 client 实例；配置变更需重启进程（与 settings 一致）。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from agent_eval.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_lark_client() -> Any | None:
    """返回共享的 app 身份 lark.Client；未配置 / SDK 缺失时返回 None。

    调用方约定：拿到 None 要给出可读错误（如「飞书未配置」），不要静默当成
    调用成功。返回值缓存，进程内复用同一 client（SDK 内部管理 token 刷新）。
    """
    if not settings.feishu.configured:
        logger.info("feishu not configured; get_lark_client() returns None")
        return None
    try:
        import lark_oapi as lark
    except ImportError:
        logger.warning("lark-oapi not installed; get_lark_client() returns None")
        return None

    return (
        lark.Client.builder()
        .app_id(settings.feishu.app_id)
        .app_secret(settings.feishu.app_secret)
        .build()
    )
