from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from agent_eval.config_service import config_service
from agent_eval.data import converter
from agent_eval.data._utils import to_thread
from agent_eval.data.provider import DatasetInfo, VersionInfo
from agent_eval.models.test_case import TestCase

logger = logging.getLogger(__name__)

_LIST_CACHE_TTL_S = 60.0
_LIST_CACHE_STALE_S = 3600.0
_LIST_COLD_WAIT_S = 2.0
_CASE_CACHE_TTL_S = 60.0
_CASE_CACHE_STALE_S = 900.0


@dataclass
class _CacheEntry:
    value: Any
    stored_at: float


@dataclass
class _CaseSnapshot:
    cases: list[TestCase]
    splits: list[str | None]


# FastAPI 每次依赖解析都会新建 provider/manager，因此缓存必须跨实例共享。
# scope 使用 Langfuse ResourceManager 的对象身份：同一 SDK 连接共享缓存，不同连接隔离。
_list_cache: dict[int, _CacheEntry] = {}
_snapshot_cache: dict[tuple[int, str, str], _CacheEntry] = {}
_page_cache: dict[tuple[int, str, str, int, int], _CacheEntry] = {}
_count_cache: dict[tuple[int, str], int] = {}
_list_generation: dict[int, int] = {}
_dataset_generation: dict[tuple[int, str], int] = {}
_dataset_scope_generation: dict[int, int] = {}
_inflight: dict[tuple[Any, ...], asyncio.Task[Any]] = {}


async def build_langfuse_client() -> Any:
    """Construct a Langfuse SDK client from the active connection preset."""
    conn = await config_service.get_langfuse_connection()
    if not conn["configured"]:
        raise RuntimeError(
            "Langfuse 未配置（缺 host / public_key / secret_key）。"
            "数据集存储已切换到 Langfuse，请在 配置 → langfuse.connection 中填写。"
        )
    try:
        from langfuse import Langfuse
    except ImportError as e:  # pragma: no cover - SDK is a hard dep in prod
        raise RuntimeError("langfuse SDK 未安装") from e

    # SDK 默认请求 timeout=5s。LangfuseResourceManager 按 public_key 单例，后续同 key
    # 重建客户端时 timeout/httpx_client 参数不会生效，因此这里不伪装成可逐请求调参。
    return Langfuse(
        public_key=conn["public_key"],
        secret_key=conn["secret_key"],
        host=conn["host"],
    )


def _as_utc(dt: datetime | None) -> datetime | None:
    """Langfuse 的 version 时间点查询要求 UTC tz-aware。把 naive 视为 UTC。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _version_key(version: datetime | None) -> str:
    return version.isoformat() if version is not None else "latest"


def _fresh(entry: _CacheEntry | None, ttl: float) -> bool:
    return entry is not None and time.monotonic() - entry.stored_at <= ttl


def _usable_stale(entry: _CacheEntry | None, max_age: float) -> bool:
    return entry is not None and time.monotonic() - entry.stored_at <= max_age


def _background_task(
    key: tuple[Any, ...],
    factory: Callable[[], Coroutine[Any, Any, Any]],
) -> asyncio.Task[Any]:
    """按 key 合并同一 event loop 内的并发上游请求。"""
    loop = asyncio.get_running_loop()
    task = _inflight.get(key)
    if task is not None and not task.done() and task.get_loop() is loop:
        return task

    task = loop.create_task(factory())
    _inflight[key] = task

    def _done(done: asyncio.Task[Any]) -> None:
        if _inflight.get(key) is done:
            _inflight.pop(key, None)
        # stale-while-revalidate 的后台任务可能没有直接 await 者，主动取 exception
        # 防止 asyncio 输出 "Task exception was never retrieved"。
        if not done.cancelled():
            try:
                done.exception()
            except Exception:
                pass

    task.add_done_callback(_done)
    return task


class LangfuseDatasetProvider:
    """DatasetProvider backed by a self-hosted Langfuse instance.

    SDK 调用是阻塞的，统一放入线程。读路径使用进程级 TTL 缓存与 single-flight：
    - 数据集列表过期后立即返回 stale，并在后台刷新；冷启动最多等待 2 秒。
    - 无筛选样例列表使用 Langfuse dataset_items 真分页并缓存每一页。
    - search/category/tag/split 等需客户端过滤的场景复用全量 snapshot 缓存。
    所有写操作在成功后失效相关缓存，避免把旧内容长期留给 UI。
    """

    def __init__(self, client: Any):
        self.client = client
        self._scope = id(getattr(client, "_resources", client))

    def _invalidate_list(self) -> None:
        _list_cache.pop(self._scope, None)
        _list_generation[self._scope] = _list_generation.get(self._scope, 0) + 1

    def _dataset_generation_token(self, dataset_name: str) -> tuple[int, int]:
        return (
            _dataset_scope_generation.get(self._scope, 0),
            _dataset_generation.get((self._scope, dataset_name), 0),
        )

    def _invalidate_dataset(self, dataset_name: str) -> None:
        _count_cache.pop((self._scope, dataset_name), None)
        generation_key = (self._scope, dataset_name)
        _dataset_generation[generation_key] = _dataset_generation.get(generation_key, 0) + 1
        for cache in (_snapshot_cache, _page_cache):
            for key in [k for k in cache if k[0] == self._scope and k[1] == dataset_name]:
                cache.pop(key, None)

    def _invalidate_all_datasets(self) -> None:
        _dataset_scope_generation[self._scope] = (
            _dataset_scope_generation.get(self._scope, 0) + 1
        )
        for key in [k for k in _count_cache if k[0] == self._scope]:
            _count_cache.pop(key, None)
        for cache in (_snapshot_cache, _page_cache):
            for key in [k for k in cache if k[0] == self._scope]:
                cache.pop(key, None)

    # ---- Dataset CRUD ----

    async def create_dataset(
        self, name: str, description: str = "", metadata: dict | None = None
    ) -> str:
        ds = await to_thread(
            self.client.create_dataset,
            name=name,
            description=description or None,
            metadata=metadata or None,
        )
        self._invalidate_list()
        self._invalidate_dataset(name)
        logger.info("Created Langfuse dataset '%s' (id=%s)", name, getattr(ds, "id", "?"))
        return str(getattr(ds, "id", name))

    async def _refresh_dataset_list(self) -> list[DatasetInfo]:
        page_limit = 100
        generation = _list_generation.get(self._scope, 0)

        def _list() -> list[Any]:
            out: list[Any] = []
            page = 1
            while True:
                resp = self.client.api.datasets.list(page=page, limit=page_limit)
                data = list(getattr(resp, "data", []) or [])
                out.extend(data)
                if len(data) < page_limit:
                    break
                page += 1
            return out

        datasets = await to_thread(_list)
        infos = [
            DatasetInfo(
                id=str(getattr(ds, "id", getattr(ds, "name", ""))),
                name=getattr(ds, "name", ""),
                description=getattr(ds, "description", "") or "",
                example_count=0,
                created_at=getattr(ds, "created_at", None) or datetime.now(timezone.utc),
                metadata=getattr(ds, "metadata", None) or {},
            )
            for ds in datasets
        ]
        if generation == _list_generation.get(self._scope, 0):
            _list_cache[self._scope] = _CacheEntry(infos, time.monotonic())
        return infos

    def _decorate_counts(
        self, datasets: list[DatasetInfo], name_contains: str | None
    ) -> list[DatasetInfo]:
        return [
            replace(ds, example_count=_count_cache.get((self._scope, ds.name), 0))
            for ds in datasets
            if not name_contains or name_contains in ds.name
        ]

    async def list_datasets(self, name_contains: str | None = None) -> list[DatasetInfo]:
        entry = _list_cache.get(self._scope)
        if _fresh(entry, _LIST_CACHE_TTL_S):
            return self._decorate_counts(entry.value, name_contains)

        key = ("dataset-list", self._scope)
        task = _background_task(key, self._refresh_dataset_list)
        if _usable_stale(entry, _LIST_CACHE_STALE_S):
            return self._decorate_counts(entry.value, name_contains)

        # 冷启动不让公网 Langfuse 把页面卡满 SDK 的 5 秒连接超时。超时后 task
        # 继续在后台完成并填缓存，router 同时用本地 metadata 快速降级。
        datasets = await asyncio.wait_for(asyncio.shield(task), timeout=_LIST_COLD_WAIT_S)
        return self._decorate_counts(datasets, name_contains)

    async def get_dataset(self, name: str) -> DatasetInfo:
        datasets = await self.list_datasets()
        dataset = next((ds for ds in datasets if ds.name == name), None)
        if dataset is None:
            raise LookupError(f"Dataset '{name}' not found")
        _, total = await self.load_cases_page(name, page=1, page_size=1)
        return replace(dataset, example_count=total)

    async def delete_dataset(self, name: str) -> None:
        """Langfuse 无删库 API：清空该 dataset 的所有 items（尽力而为）。"""
        def _empty() -> int:
            try:
                dc = self.client.get_dataset(name)
            except Exception as e:
                logger.warning("Langfuse dataset '%s' 取不到，跳过清空 items：%s", name, e)
                return 0
            items = list(getattr(dc, "items", []) or [])
            for it in items:
                item_id = getattr(it, "id", None)
                if item_id:
                    self.client.api.dataset_items.delete(item_id)
            return len(items)

        n = await to_thread(_empty)
        self._invalidate_list()
        self._invalidate_dataset(name)
        logger.info("Emptied Langfuse dataset '%s' (%d items)", name, n)

    # ---- Example / TestCase CRUD ----

    async def add_case(
        self, dataset_name: str, case: TestCase, split: str | None = None
    ) -> str:
        params = converter.case_to_dataset_item(case, split=split)
        item = await to_thread(
            self.client.create_dataset_item,
            dataset_name=dataset_name,
            input=params["input"],
            expected_output=params["expected_output"],
            metadata=params["metadata"],
            id=params["id"],
        )
        self._invalidate_dataset(dataset_name)
        return str(getattr(item, "id", params["id"]))

    async def add_cases_batch(
        self,
        dataset_name: str,
        cases: list[TestCase],
        split: str | None = None,
        source_run_ids: list[str] | None = None,
    ) -> list[str]:
        all_params = [converter.case_to_dataset_item(c, split=split) for c in cases]

        def _create_all() -> list[str]:
            ids: list[str] = []
            for params in all_params:
                item = self.client.create_dataset_item(
                    dataset_name=dataset_name,
                    input=params["input"],
                    expected_output=params["expected_output"],
                    metadata=params["metadata"],
                    id=params["id"],
                )
                ids.append(str(getattr(item, "id", params["id"])))
            return ids

        ids = await to_thread(_create_all)
        self._invalidate_dataset(dataset_name)
        return ids

    async def _refresh_snapshot(
        self, dataset_name: str, version: datetime | None, cache_key: tuple[int, str, str]
    ) -> _CaseSnapshot:
        generation = self._dataset_generation_token(dataset_name)

        def _load() -> list[Any]:
            dc = self.client.get_dataset(dataset_name, version=version)
            return list(getattr(dc, "items", []) or [])

        items = await to_thread(_load)
        snapshot = _CaseSnapshot(
            cases=[converter.dataset_item_to_test_case(item) for item in items],
            splits=[(getattr(item, "metadata", None) or {}).get("split") for item in items],
        )
        if generation == self._dataset_generation_token(dataset_name):
            _snapshot_cache[cache_key] = _CacheEntry(snapshot, time.monotonic())
            if version is None:
                _count_cache[(self._scope, dataset_name)] = len(snapshot.cases)
        return snapshot

    async def _get_snapshot(
        self, dataset_name: str, version: datetime | None
    ) -> _CaseSnapshot:
        cache_key = (self._scope, dataset_name, _version_key(version))
        entry = _snapshot_cache.get(cache_key)
        if _fresh(entry, _CASE_CACHE_TTL_S):
            return entry.value

        task_key = ("dataset-snapshot",) + cache_key
        task = _background_task(
            task_key,
            lambda: self._refresh_snapshot(dataset_name, version, cache_key),
        )
        if _usable_stale(entry, _CASE_CACHE_STALE_S):
            return entry.value
        return await task

    async def load_cases_page(
        self,
        dataset_name: str,
        *,
        page: int,
        page_size: int,
        as_of: datetime | None = None,
    ) -> tuple[list[TestCase], int]:
        """直接使用 Langfuse dataset_items 分页，避免每次翻页拉完整数据集。"""
        version = _as_utc(as_of)
        cache_key = (self._scope, dataset_name, _version_key(version), page, page_size)
        entry = _page_cache.get(cache_key)
        if _fresh(entry, _CASE_CACHE_TTL_S):
            return entry.value

        generation = self._dataset_generation_token(dataset_name)

        async def _refresh() -> tuple[list[TestCase], int]:
            response = await to_thread(
                self.client.api.dataset_items.list,
                dataset_name=dataset_name,
                version=version,
                page=page,
                limit=page_size,
            )
            items = list(getattr(response, "data", []) or [])
            meta = getattr(response, "meta", None)
            total = int(getattr(meta, "total_items", len(items)))
            result = ([converter.dataset_item_to_test_case(item) for item in items], total)
            if generation == self._dataset_generation_token(dataset_name):
                _page_cache[cache_key] = _CacheEntry(result, time.monotonic())
                if version is None:
                    _count_cache[(self._scope, dataset_name)] = total
            return result

        task_key = ("dataset-page",) + cache_key
        task = _background_task(task_key, _refresh)
        if _usable_stale(entry, _CASE_CACHE_STALE_S):
            return entry.value
        return await task

    async def load_cases(
        self,
        dataset_name: str,
        *,
        as_of: datetime | None = None,
        splits: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> list[TestCase]:
        snapshot = await self._get_snapshot(dataset_name, _as_utc(as_of))
        cases = snapshot.cases

        if splits:
            split_set = set(splits)
            cases = [
                case for case, split in zip(snapshot.cases, snapshot.splits)
                if split in split_set
            ]
        if tags:
            tag_set = set(tags)
            cases = [case for case in cases if tag_set.intersection(case.tags)]
        if limit:
            cases = cases[:limit]
        return list(cases)

    async def get_case(self, example_id: str) -> TestCase | None:
        try:
            item = await to_thread(self.client.api.dataset_items.get, example_id)
        except Exception:
            return None
        if item is None:
            return None
        return converter.dataset_item_to_test_case(item)

    async def update_case(self, example_id: str, case: TestCase) -> None:
        existing = await to_thread(self.client.api.dataset_items.get, example_id)
        dataset_name = getattr(existing, "dataset_name", None)
        if not dataset_name:
            raise RuntimeError(f"无法定位 item {example_id} 的所属数据集")
        params = converter.case_to_dataset_item(case)
        await to_thread(
            self.client.create_dataset_item,
            dataset_name=dataset_name,
            input=params["input"],
            expected_output=params["expected_output"],
            metadata=params["metadata"],
            id=example_id,
        )
        self._invalidate_dataset(dataset_name)

    async def delete_case(self, example_id: str) -> None:
        dataset_name: str | None = None
        try:
            existing = await to_thread(self.client.api.dataset_items.get, example_id)
            dataset_name = getattr(existing, "dataset_name", None)
        except Exception:
            pass
        await to_thread(self.client.api.dataset_items.delete, example_id)
        if dataset_name:
            self._invalidate_dataset(dataset_name)
        else:
            self._invalidate_all_datasets()

    async def delete_cases_batch(self, example_ids: list[str]) -> None:
        def _delete_all() -> None:
            for example_id in example_ids:
                self.client.api.dataset_items.delete(example_id)

        await to_thread(_delete_all)
        self._invalidate_all_datasets()

    # ---- Versioning ----

    async def list_versions(self, dataset_name: str) -> list[VersionInfo]:
        return []

    async def pull_external_dataset(
        self, source_dataset_name: str, *, limit: int | None = None
    ) -> list[TestCase]:
        raise NotImplementedError(
            "从外部 LangSmith 数据集拉取仍由 LangSmith provider 处理，"
            "Langfuse provider 不支持该操作。"
        )
