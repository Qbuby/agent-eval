from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from agent_eval.config_service import config_service
from agent_eval.data import converter
from agent_eval.data._utils import to_thread
from agent_eval.data.provider import DatasetInfo, VersionInfo
from agent_eval.models.test_case import TestCase

logger = logging.getLogger(__name__)


async def build_langfuse_client() -> Any:
    """Construct a Langfuse SDK client from the active connection preset.

    Mirrors the pattern in ``evaluation/langfuse_sync.py`` — resolves
    ``langfuse.connection`` (falling back to env settings) via config_service,
    so dataset storage uses the same self-hosted instance as trace sync. Raises
    if Langfuse isn't configured, since dataset storage now depends on it.
    """
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


class LangfuseDatasetProvider:
    """DatasetProvider backed by a self-hosted Langfuse instance.

    Implements the same Protocol as LangSmithDatasetProvider. The Langfuse SDK
    is blocking, so every call is wrapped in ``to_thread``. Notable capability
    differences vs LangSmith (handled here):

    - ``create_dataset`` is an idempotent upsert (no 409 on duplicate name).
    - Items are created with an explicit ``id`` so re-creating == update.
    - Langfuse has no "delete dataset" API — ``delete_dataset`` empties items;
      the router additionally soft-hides the dataset via local metadata.
    - No "version list" API — ``list_versions`` returns []; point-in-time
      snapshots (``load_cases(as_of=)``) ARE supported via the ``version`` arg.
    - ``pull_external_dataset`` stays a LangSmith-only feature.
    """

    def __init__(self, client: Any):
        self.client = client

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
        logger.info("Created Langfuse dataset '%s' (id=%s)", name, getattr(ds, "id", "?"))
        return str(getattr(ds, "id", name))

    async def list_datasets(self, name_contains: str | None = None) -> list[DatasetInfo]:
        page_limit = 100

        def _list() -> list[Any]:
            out: list[Any] = []
            page = 1
            while True:
                resp = self.client.api.datasets.list(page=page, limit=page_limit)
                data = list(getattr(resp, "data", []) or [])
                out.extend(data)
                # 不依赖 meta（SDK 可能省略它）：空页或不满页即终止，满页则续翻。
                # 这样 >100 个数据集且响应缺 meta 时也不会静默截断到前 100 条。
                if len(data) < page_limit:
                    break
                page += 1
            return out

        datasets = await to_thread(_list)
        infos: list[DatasetInfo] = []
        for ds in datasets:
            ds_name = getattr(ds, "name", "")
            if name_contains and name_contains not in ds_name:
                continue
            infos.append(DatasetInfo(
                id=str(getattr(ds, "id", ds_name)),
                name=ds_name,
                description=getattr(ds, "description", "") or "",
                # Langfuse Dataset 无 item 计数字段；列表场景不逐个拉 items（昂贵），
                # 真实计数由 get_dataset 或上层本地表提供。
                example_count=0,
                created_at=getattr(ds, "created_at", None) or datetime.now(timezone.utc),
                metadata=getattr(ds, "metadata", None) or {},
            ))
        return infos

    async def get_dataset(self, name: str) -> DatasetInfo:
        dc = await to_thread(self.client.get_dataset, name)
        items = list(getattr(dc, "items", []) or [])
        return DatasetInfo(
            id=str(getattr(dc, "id", name)),
            name=getattr(dc, "name", name),
            description=getattr(dc, "description", "") or "",
            example_count=len(items),
            created_at=getattr(dc, "created_at", None) or datetime.now(timezone.utc),
            metadata=getattr(dc, "metadata", None) or {},
        )

    async def delete_dataset(self, name: str) -> None:
        """Langfuse 无删库 API：清空该 dataset 的所有 items（尽力而为）。
        整库的「隐藏」由 router 在本地 dataset_metadata 标记 status=deleted 完成。

        云端不存在该 dataset 时（跨环境创建 / 状态漂移）静默当作 0 items —— 删除
        的语义目标是「让它消失」，本地软删标记仍须落地，不能因云端缺失而抛错。"""
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
        return str(getattr(item, "id", params["id"]))

    async def add_cases_batch(
        self,
        dataset_name: str,
        cases: list[TestCase],
        split: str | None = None,
        source_run_ids: list[str] | None = None,
    ) -> list[str]:
        # Langfuse 无批量创建接口；在单个工作线程里顺序 upsert，避免线程爆炸。
        # source_run_ids 在 Langfuse 侧无直接对应（仅 LangSmith 用于血缘），忽略。
        all_params = [converter.case_to_dataset_item(c, split=split) for c in cases]

        def _create_all() -> list[str]:
            ids: list[str] = []
            for p in all_params:
                item = self.client.create_dataset_item(
                    dataset_name=dataset_name,
                    input=p["input"],
                    expected_output=p["expected_output"],
                    metadata=p["metadata"],
                    id=p["id"],
                )
                ids.append(str(getattr(item, "id", p["id"])))
            return ids

        return await to_thread(_create_all)

    async def load_cases(
        self,
        dataset_name: str,
        *,
        as_of: datetime | None = None,
        splits: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> list[TestCase]:
        version = _as_utc(as_of)

        def _load() -> list[Any]:
            dc = self.client.get_dataset(dataset_name, version=version)
            return list(getattr(dc, "items", []) or [])

        items = await to_thread(_load)
        cases = [converter.dataset_item_to_test_case(it) for it in items]

        # Langfuse 无 split 一等概念：split 存在 metadata 里，这里客户端过滤。
        if splits:
            split_set = set(splits)
            cases = [
                c for it, c in zip(items, cases)
                if (getattr(it, "metadata", None) or {}).get("split") in split_set
            ]

        if tags:
            tag_set = set(tags)
            cases = [c for c in cases if tag_set.intersection(c.tags)]

        if limit:
            cases = cases[:limit]

        return cases

    async def update_case(self, example_id: str, case: TestCase) -> None:
        # Langfuse upsert 需要 dataset_name，但 update 入参不带。先按 id 取回
        # 现有 item 学到它的 dataset_name，再用同 id upsert（== 更新）。
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

    async def delete_case(self, example_id: str) -> None:
        await to_thread(self.client.api.dataset_items.delete, example_id)

    async def delete_cases_batch(self, example_ids: list[str]) -> None:
        def _delete_all() -> None:
            for eid in example_ids:
                self.client.api.dataset_items.delete(eid)

        await to_thread(_delete_all)

    # ---- Versioning ----

    async def list_versions(self, dataset_name: str) -> list[VersionInfo]:
        # Langfuse 无「版本列表」API（只支持按 datetime 取时间点快照）。
        # 该能力缺口降级为返回空列表；load_cases(as_of=) 仍可用。
        return []

    # ---- Pull external dataset (LangSmith-only) ----

    async def pull_external_dataset(
        self, source_dataset_name: str, *, limit: int | None = None
    ) -> list[TestCase]:
        raise NotImplementedError(
            "从外部 LangSmith 数据集拉取仍由 LangSmith provider 处理，"
            "Langfuse provider 不支持该操作。"
        )
