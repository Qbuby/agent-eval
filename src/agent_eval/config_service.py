from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, select

from agent_eval.config import settings
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import SystemConfigRow

SENSITIVE_PREFIXES = ("auth.", "llm.api_key", "db.")

DEFAULT_CONFIGS: list[dict[str, Any]] = [
    {
        "key": "langsmith.api_url",
        "value": {"v": settings.langsmith.api_url},
        "category": "langsmith",
        "description": "LangSmith API 地址",
    },
    {
        "key": "langsmith.project_name",
        "value": {"v": settings.langsmith.project_name},
        "category": "langsmith",
        "description": "默认项目名",
    },
    {
        "key": "langsmith.default_dataset",
        "value": {"v": settings.langsmith.default_dataset},
        "category": "langsmith",
        "description": "默认数据集",
    },
    {
        "key": "scheduler.poll_interval_seconds",
        "value": {"v": 60},
        "category": "scheduler",
        "description": "轮询间隔（秒）",
    },
    {
        "key": "scheduler.enabled",
        "value": {"v": True},
        "category": "scheduler",
        "description": "调度器开关",
    },
    {
        "key": "routing.default_dataset",
        "value": {"v": ""},
        "category": "routing",
        "description": "默认路由目标数据集",
    },
]


class ConfigService:
    def __init__(self, cache_ttl: float = 60.0):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._cache_ttl = cache_ttl
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
        parts = key.split(".", 1)
        if len(parts) != 2:
            return None
        section, field = parts
        section_map = {
            "langsmith": settings.langsmith,
            "scheduler": None,
            "routing": None,
            "general": None,
        }
        obj = section_map.get(section)
        if obj is None:
            return None
        return getattr(obj, field, None)

    async def get(self, key: str) -> Any | None:
        if self._is_cached(key):
            return self._cache[key][0]

        async with async_session_factory() as session:
            result = await session.execute(
                select(SystemConfigRow).where(SystemConfigRow.key == key)
            )
            row = result.scalar_one_or_none()

        if row is not None:
            value = row.value.get("v") if isinstance(row.value, dict) else row.value
            self._cache[key] = (value, time.time())
            return value

        fallback = self._get_env_fallback(key)
        if fallback is not None:
            self._cache[key] = (fallback, time.time())
        return fallback

    async def set(self, key: str, value: Any, user_id: uuid.UUID | None = None) -> SystemConfigRow:
        async with async_session_factory() as session:
            result = await session.execute(
                select(SystemConfigRow).where(SystemConfigRow.key == key)
            )
            row = result.scalar_one_or_none()

            if row is not None:
                row.value = {"v": value}
                row.updated_by = user_id
                from datetime import datetime, timezone
                row.updated_at = datetime.now(timezone.utc)
            else:
                row = SystemConfigRow(
                    key=key,
                    value={"v": value},
                    category=self._infer_category(key),
                    updated_by=user_id,
                )
                session.add(row)

            await session.commit()
            await session.refresh(row)

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

        self._cache.pop(key, None)
        return result.rowcount > 0

    async def get_all_by_category(self, category: str) -> list[SystemConfigRow]:
        return await self.list(category=category)

    async def batch_set(
        self, items: dict[str, Any], user_id: uuid.UUID | None = None
    ) -> list[SystemConfigRow]:
        results = []
        for key, value in items.items():
            row = await self.set(key, value, user_id)
            results.append(row)
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
        prefix = key.split(".")[0]
        if prefix in ("langsmith", "scheduler", "routing"):
            return prefix
        return "general"

    @staticmethod
    def is_sensitive(key: str) -> bool:
        return any(key.startswith(p) for p in SENSITIVE_PREFIXES)


config_service = ConfigService()
