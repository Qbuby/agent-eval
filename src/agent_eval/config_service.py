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


# ─── Storage shape ──────────────────────────────────────────────────────────
# A config row's `value` JSONB is now an "options bag":
#   {"options": [{"value": <any>, "label": <str|null>}, ...], "default_index": 0}
#
# - Backwards compatible read: rows still in {"v": x} legacy shape are
#   translated to single-option on the fly. They get rewritten to the new
#   shape on the next set/add.
# - Single-value consumers (settings, retry config, agents) keep using
#   `config_service.get(key)` which returns the *default* option's scalar.

def _normalize_options(raw: Any) -> tuple[list[dict[str, Any]], int]:
    """Return (options, default_index) from any historical JSONB shape."""
    if isinstance(raw, dict):
        if "options" in raw and isinstance(raw["options"], list):
            opts = []
            for o in raw["options"]:
                if isinstance(o, dict) and "value" in o:
                    opts.append({"value": o["value"], "label": o.get("label")})
                else:
                    opts.append({"value": o, "label": None})
            di = raw.get("default_index", 0)
            if not isinstance(di, int) or di < 0 or di >= len(opts):
                di = 0
            if not opts:
                return [], 0
            return opts, di
        if "v" in raw:
            return [{"value": raw["v"], "label": None}], 0
    # Treat any other shape (scalar, list) as a single value.
    return [{"value": raw, "label": None}], 0


def _pack(options: list[dict[str, Any]], default_index: int) -> dict[str, Any]:
    di = default_index if 0 <= default_index < len(options) else 0
    return {"options": options, "default_index": di}


def _default_value(raw: Any) -> Any:
    opts, di = _normalize_options(raw)
    if not opts:
        return None
    return opts[di]["value"]


DEFAULT_CONFIGS: list[dict[str, Any]] = [
    {
        "key": "langsmith.api_url",
        "value": _pack([{"value": settings.langsmith.api_url, "label": "默认"}], 0),
        "category": "langsmith",
        "description": "LangSmith API 地址",
    },
    {
        "key": "langsmith.api_key",
        "value": _pack([{"value": "", "label": None}], 0),
        "category": "langsmith",
        "description": "LangSmith API Key",
    },
    {
        "key": "llm.base_url",
        "value": _pack([{"value": "", "label": None}], 0),
        "category": "llm",
        "description": "LLM 服务地址",
    },
    {
        "key": "llm.api_key",
        "value": _pack([{"value": "", "label": None}], 0),
        "category": "llm",
        "description": "LLM API Key",
    },
    {
        "key": "target_agent.endpoint_url",
        "value": _pack([{"value": "", "label": None}], 0),
        "category": "target_agent",
        "description": "测试目标模型 POST 接口地址",
    },
    {
        "key": "target_agent.api_key",
        "value": _pack([{"value": "", "label": None}], 0),
        "category": "target_agent",
        "description": "测试目标模型 API Key（如需鉴权）",
    },
    {
        "key": "target_agent.timeout",
        "value": _pack([{"value": "30", "label": None}], 0),
        "category": "target_agent",
        "description": "请求超时时间（秒）",
    },
    {
        "key": "target_agent.request_template",
        "value": _pack([{"value": "{\"query\": \"{{question}}\"}", "label": None}], 0),
        "category": "target_agent",
        "description": "请求体模板（JSON），用 {{question}} 作为问题占位符",
    },
    {
        "key": "target_agent.response_path",
        "value": _pack([{"value": "data.answer", "label": None}], 0),
        "category": "target_agent",
        "description": "从响应 JSON 中提取答案的路径（点分隔）",
    },
    {
        "key": "target_agent.headers",
        "value": _pack([{"value": "{\"Content-Type\": \"application/json\"}", "label": None}], 0),
        "category": "target_agent",
        "description": "自定义请求头（JSON 格式）",
    },
    {
        "key": "eval.retry.max_retries",
        "value": _pack([{"value": 2, "label": None}], 0),
        "category": "eval.retry",
        "description": "评估时单条 case 调 agent 失败后最多重试次数（不含首次）。0 表示不重试。",
    },
    {
        "key": "eval.retry.initial_backoff_s",
        "value": _pack([{"value": 2.0, "label": None}], 0),
        "category": "eval.retry",
        "description": "首次重试前的等待秒数（指数退避起点）。",
    },
    {
        "key": "eval.retry.backoff_factor",
        "value": _pack([{"value": 2.0, "label": None}], 0),
        "category": "eval.retry",
        "description": "每次重试退避乘数；下次等待 = 上次 × 该值。",
    },
    {
        "key": "eval.retry.max_backoff_s",
        "value": _pack([{"value": 30.0, "label": None}], 0),
        "category": "eval.retry",
        "description": "退避秒数上限，避免长尾等待。",
    },
    {
        "key": "langfuse_metrics.poll_interval_seconds",
        "value": _pack([{"value": 86400, "label": None}], 0),
        "category": "langfuse_metrics",
        "description": "Langfuse 指标轮询间隔（秒）。默认 86400（24h），最小 60。",
    },
    {
        "key": "langfuse_metrics.lookback_days",
        "value": _pack([{"value": 30, "label": None}], 0),
        "category": "langfuse_metrics",
        "description": "首次回填窗口 + 数据保留天数。增量拉取后旧于该天数的数据会被清理。",
    },
    {
        "key": "langfuse_metrics.environments",
        "value": _pack([{"value": "saas-prod,xinchai-prod,smartlink-hc-dev", "label": None}], 0),
        "category": "langfuse_metrics",
        "description": "拉取的目标环境列表，逗号分隔。留空则用内置默认。",
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
        """Return the *default* option's scalar value, for back-compat."""
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
            value = _default_value(row.value)
            async with self._lock:
                self._cache[key] = (value, time.time())
            return value

        fallback = self._get_env_fallback(key)
        if fallback is not None:
            async with self._lock:
                self._cache[key] = (fallback, time.time())
        return fallback

    async def get_options(self, key: str) -> tuple[list[dict[str, Any]], int]:
        """Return all options and the default index for `key`."""
        async with async_session_factory() as session:
            result = await session.execute(
                select(SystemConfigRow).where(SystemConfigRow.key == key)
            )
            row = result.scalar_one_or_none()
        if row is None:
            return [], 0
        return _normalize_options(row.value)

    async def set(
        self,
        key: str,
        value: Any,
        user_id: uuid.UUID | None = None,
        description: str | None = None,
    ) -> SystemConfigRow:
        """Single-value set: replace options with a single entry. Keeps
        backwards compatibility with callers that still treat config as 1:1."""
        return await self._mutate(
            key,
            lambda _opts, _di: ([{"value": value, "label": None}], 0),
            user_id=user_id,
            description=description,
        )

    async def add_option(
        self,
        key: str,
        value: Any,
        label: str | None = None,
        make_default: bool = False,
        user_id: uuid.UUID | None = None,
        description: str | None = None,
    ) -> SystemConfigRow:
        def mutate(opts: list[dict[str, Any]], di: int) -> tuple[list[dict[str, Any]], int]:
            new_opts = list(opts) + [{"value": value, "label": label}]
            new_di = len(new_opts) - 1 if make_default else di
            return new_opts, new_di

        return await self._mutate(key, mutate, user_id=user_id, description=description)

    async def update_option(
        self,
        key: str,
        index: int,
        value: Any,
        label: str | None = None,
        user_id: uuid.UUID | None = None,
    ) -> SystemConfigRow:
        def mutate(opts: list[dict[str, Any]], di: int) -> tuple[list[dict[str, Any]], int]:
            if not opts or index < 0 or index >= len(opts):
                raise IndexError(f"option index {index} out of range")
            new_opts = list(opts)
            new_opts[index] = {"value": value, "label": label}
            return new_opts, di

        return await self._mutate(key, mutate, user_id=user_id)

    async def remove_option(
        self,
        key: str,
        index: int,
        user_id: uuid.UUID | None = None,
    ) -> SystemConfigRow:
        def mutate(opts: list[dict[str, Any]], di: int) -> tuple[list[dict[str, Any]], int]:
            if not opts or index < 0 or index >= len(opts):
                raise IndexError(f"option index {index} out of range")
            if len(opts) == 1:
                raise ValueError("cannot remove the last remaining option; delete the key instead")
            new_opts = [o for i, o in enumerate(opts) if i != index]
            new_di = di
            if index == di:
                new_di = 0
            elif index < di:
                new_di = di - 1
            return new_opts, new_di

        return await self._mutate(key, mutate, user_id=user_id)

    async def set_default(
        self,
        key: str,
        index: int,
        user_id: uuid.UUID | None = None,
    ) -> SystemConfigRow:
        def mutate(opts: list[dict[str, Any]], _di: int) -> tuple[list[dict[str, Any]], int]:
            if not opts or index < 0 or index >= len(opts):
                raise IndexError(f"option index {index} out of range")
            return opts, index

        return await self._mutate(key, mutate, user_id=user_id)

    async def _mutate(
        self,
        key: str,
        mutate: Callable[[list[dict[str, Any]], int], tuple[list[dict[str, Any]], int]],
        user_id: uuid.UUID | None = None,
        description: str | None = None,
    ) -> SystemConfigRow:
        async with async_session_factory() as session:
            result = await session.execute(
                select(SystemConfigRow).where(SystemConfigRow.key == key)
            )
            row = result.scalar_one_or_none()

            if row is not None:
                opts, di = _normalize_options(row.value)
            else:
                opts, di = [], 0

            new_opts, new_di = mutate(opts, di)
            packed = _pack(new_opts, new_di)

            if row is not None:
                row.value = packed
                row.updated_by = user_id
                row.updated_at = datetime.now(timezone.utc)
                if description is not None:
                    row.description = description
            else:
                row = SystemConfigRow(
                    key=key,
                    value=packed,
                    category=self._infer_category(key),
                    description=description,
                    updated_by=user_id,
                )
                session.add(row)

            await session.commit()
            await session.refresh(row)

        new_default = _default_value(row.value)
        async with self._lock:
            self._cache[key] = (new_default, time.time())
        self._notify(key, new_default)
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
        results: list[SystemConfigRow] = []
        for key, value in items.items():
            results.append(await self.set(key, value, user_id=user_id))
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
        if prefix in ("langsmith", "llm", "target_agent", "langfuse_metrics"):
            return prefix
        return "general"

    @staticmethod
    def is_sensitive(key: str) -> bool:
        return any(key.startswith(p) for p in SENSITIVE_PREFIXES)

    @staticmethod
    def normalize_options(raw: Any) -> tuple[list[dict[str, Any]], int]:
        return _normalize_options(raw)

    @staticmethod
    def default_value(raw: Any) -> Any:
        return _default_value(raw)


config_service = ConfigService()
