from __future__ import annotations

import asyncio
import functools
from typing import Any

from agent_eval.data.content_blocks import ContentValidationError, normalize_content


async def to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(functools.partial(func, *args, **kwargs))


def _coerce_content(content: Any) -> Any:
    """把一条消息的 content 收敛成 canonical 形状（字符串或 content blocks）。

    这里必须容错：本函数服务于 trace 抽取与文件导入，喂进来的是外部数据，
    可能含 ``tool_use`` / ``thinking`` 等本项目 content blocks 不认的块类型。
    这类结构走老路径 ``str(content)`` 兜底，避免把原先能跑通的抽取变成硬失败；
    真正的附件块（image/document/video）则被完整保留下来。
    """
    if isinstance(content, str):
        return content
    try:
        return normalize_content(content)
    except ContentValidationError:
        return str(content)


def normalize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    normalized = []
    for msg in messages:
        if isinstance(msg, str):
            normalized.append({"role": "user", "content": msg})
        elif isinstance(msg, dict):
            role = msg.get("role") or msg.get("type", "user")
            content = msg.get("content", msg.get("text", ""))
            normalized.append({
                "role": role_map.get(role, role),
                "content": _coerce_content(content),
            })
        elif isinstance(msg, (list, tuple)) and len(msg) == 2:
            normalized.append({"role": str(msg[0]), "content": _coerce_content(msg[1])})
        else:
            role = getattr(msg, "type", "user")
            normalized.append({
                "role": role_map.get(role, role),
                "content": _coerce_content(getattr(msg, "content", str(msg))),
            })
    return normalized


def truncate(text: str, max_len: int) -> str:
    return text[:max_len] + "..." if len(text) > max_len else text
