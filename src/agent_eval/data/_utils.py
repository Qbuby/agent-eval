from __future__ import annotations

import asyncio
import functools
from typing import Any


async def to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(functools.partial(func, *args, **kwargs))


def normalize_messages(messages: list[Any]) -> list[dict[str, str]]:
    role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    normalized = []
    for msg in messages:
        if isinstance(msg, str):
            normalized.append({"role": "user", "content": msg})
        elif isinstance(msg, dict):
            role = msg.get("role") or msg.get("type", "user")
            content = msg.get("content", msg.get("text", ""))
            normalized.append({"role": role_map.get(role, role), "content": str(content)})
        elif isinstance(msg, (list, tuple)) and len(msg) == 2:
            normalized.append({"role": str(msg[0]), "content": str(msg[1])})
        else:
            role = getattr(msg, "type", "user")
            normalized.append({
                "role": role_map.get(role, role),
                "content": getattr(msg, "content", str(msg)),
            })
    return normalized


def truncate(text: str, max_len: int) -> str:
    return text[:max_len] + "..." if len(text) > max_len else text
