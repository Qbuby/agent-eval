from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from agent_eval.config import settings
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import SystemConfigRow

SENSITIVE_PREFIXES = ("auth.", "db.")

DEFAULT_CONFIGS: list[dict[str, Any]] = [
    {
        "key": "langsmith.api_url",
        "value": {"v": settings.langsmith.api_url},
        "category": "langsmith",
        "description": "LangSmith API 地址",
    },
    {
        "key": "langsmith.api_key",
        "value": {"v": ""},
        "category": "langsmith",
        "description": "LangSmith API Key",
    },
    {
        "key": "llm.base_url",
        "value": {"v": ""},
        "category": "llm",
        "description": "LLM 服务地址",
    },
    {
        "key": "llm.api_key",
        "value": {"v": ""},
        "category": "llm",
        "description": "LLM API Key",
    },
    {
        "key": "target_agent.endpoint_url",
        "value": {"v": ""},
        "category": "target_agent",
        "description": "测试目标模型 POST 接口地址",
    },
    {
        "key": "target_agent.api_key",
        "value": {"v": ""},
        "category": "target_agent",
        "description": "测试目标模型 API Key（如需鉴权）",
    },
    {
        "key": "target_agent.timeout",
        "value": {"v": "30"},
        "category": "target_agent",
        "description": "请求超时时间（秒）",
    },
    {
        "key": "target_agent.request_template",
        "value": {"v": "{\"query\": \"{{question}}\"}"},
        "category": "target_agent",
        "description": "请求体模板（JSON），用 {{question}} 作为问题占位符",
    },
    {
        "key": "target_agent.response_path",
        "value": {"v": "data.answer"},
        "category": "target_agent",
        "description": "从响应 JSON 中提取答案的路径（点分隔）",
    },
    {
        "key": "target_agent.headers",
        "value": {"v": "{\"Content-Type\": \"application/json\"}"},
        "category": "target_agent",
        "description": "自定义请求头（JSON 格式）",
    },
    {
        "key": "eval.retry.max_retries",
        "value": {"v": 2},
        "category": "eval.retry",
        "description": "评估时单条 case 调 agent 失败后最多重试次数（不含首次）。0 表示不重试。",
    },
    {
        "key": "eval.retry.initial_backoff_s",
        "value": {"v": 2.0},
        "category": "eval.retry",
        "description": "首次重试前的等待秒数（指数退避起点）。",
    },
    {
        "key": "eval.retry.backoff_factor",
        "value": {"v": 2.0},
        "category": "eval.retry",
        "description": "每次重试退避乘数；下次等待 = 上次 × 该值。",
    },
    {
        "key": "eval.retry.max_backoff_s",
        "value": {"v": 30.0},
        "category": "eval.retry",
        "description": "退避秒数上限，避免长尾等待。",
    },
]


class ConfigService:
    def __init__(self, cache_ttl: float = 60.0):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._cache_ttl = cache_ttl
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[str, Any], None]] = []

    def on_change(self, listener: Callable[[str, Any], None]) -> None:
        self._listeners.append(listener)

    def _notify(self, key: str, value: Any) -> None:
        for listener in self._listeners:
            listener(key, value)

    def _is_cached(self, key: str) -> bool:
        if key not in self._cache:
            return False
        _, ts = self._cache[key]
        return (time.time() - ts) < self._cache_ttl

    def _get_env_fallback(self, key: str) -> Any | None:
        if self.is_sensitive(key):
            return None
        parts = key.split(".", 1)
        if len(parts) != 2:
            return None
        section, field = parts
        section_map: dict[str, Any] = {
            "langsmith": settings.langsmith,
            "llm": settings.llm if hasattr(settings, "llm") else None,
        }
        obj = section_map.get(section)
        if obj is None:
            return None
        return getattr(obj, field, None)

    async def get(self, key: str) -> Any | None:
        if self.is_sensitive(key):
            return None

        async with self._lock:
            if self._is_cached(key):
                return self._cache[key][0]

        async with async_session_factory() as session:
            result = await session.execute(
                select(SystemConfigRow).where(SystemConfigRow.key == key)
            )
            row = result.scalar_one_or_none()

        if row is not None:
            value = row.value.get("v") if isinstance(row.value, dict) else row.value
            async with self._lock:
                self._cache[key] = (value, time.time())
            return value

        fallback = self._get_env_fallback(key)
        if fallback is not None:
            async with self._lock:
                self._cache[key] = (fallback, time.time())
        return fallback

    async def set(
        self,
        key: str,
        value: Any,
        user_id: uuid.UUID | None = None,
        description: str | None = None,
    ) -> SystemConfigRow:
        async with async_session_factory() as session:
            result = await session.execute(
                select(SystemConfigRow).where(SystemConfigRow.key == key)
            )
            row = result.scalar_one_or_none()

            if row is not None:
                row.value = {"v": value}
                row.updated_by = user_id
                row.updated_at = datetime.now(timezone.utc)
                if description is not None:
                    row.description = description
            else:
                row = SystemConfigRow(
                    key=key,
                    value={"v": value},
                    category=self._infer_category(key),
                    description=description,
                    updated_by=user_id,
                )
                session.add(row)

            await session.commit()
            await session.refresh(row)

        async with self._lock:
            self._cache[key] = (value, time.time())
        self._notify(key, value)
        return row

    async def list(self, category: str | None = None) -> list[SystemConfigRow]:
        async with async_session_factory() as session:
            stmt = select(SystemConfigRow)
            if category:
                stmt = stmt.where(SystemConfigRow.category == category)
            stmt = stmt.order_by(SystemConfigRow.key)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def delete(self, key: str) -> bool:
        async with async_session_factory() as session:
            result = await session.execute(
                delete(SystemConfigRow).where(SystemConfigRow.key == key)
            )
            await session.commit()

        async with self._lock:
            self._cache.pop(key, None)
        return result.rowcount > 0

    async def get_all_by_category(self, category: str) -> list[SystemConfigRow]:
        return await self.list(category=category)

    async def batch_set(
        self, items: dict[str, Any], user_id: uuid.UUID | None = None
    ) -> list[SystemConfigRow]:
        results = []
        async with async_session_factory() as session:
            for key, value in items.items():
                result = await session.execute(
                    select(SystemConfigRow).where(SystemConfigRow.key == key)
                )
                row = result.scalar_one_or_none()

                if row is not None:
                    row.value = {"v": value}
                    row.updated_by = user_id
                    row.updated_at = datetime.now(timezone.utc)
                else:
                    row = SystemConfigRow(
                        key=key,
                        value={"v": value},
                        category=self._infer_category(key),
                        updated_by=user_id,
                    )
                    session.add(row)
                results.append(row)

            await session.commit()
            for row in results:
                await session.refresh(row)

        async with self._lock:
            now = time.time()
            for key, value in items.items():
                self._cache[key] = (value, now)

        for key, value in items.items():
            self._notify(key, value)

        return results

    async def init_defaults(self) -> None:
        async with async_session_factory() as session:
            for cfg in DEFAULT_CONFIGS:
                result = await session.execute(
                    select(SystemConfigRow).where(SystemConfigRow.key == cfg["key"])
                )
                if result.scalar_one_or_none() is None:
                    session.add(SystemConfigRow(**cfg))
            await session.commit()

    @staticmethod
    def _infer_category(key: str) -> str:
        parts = key.split(".")
        prefix = parts[0]
        if prefix == "eval" and len(parts) >= 2:
            return f"eval.{parts[1]}"
        if prefix in ("langsmith", "llm", "target_agent"):
            return prefix
        return "general"

    @staticmethod
    def is_sensitive(key: str) -> bool:
        return any(key.startswith(p) for p in SENSITIVE_PREFIXES)


config_service = ConfigService()
