from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from agent_eval.data import langfuse_provider as provider_module
from agent_eval.data.langfuse_provider import LangfuseDatasetProvider
from agent_eval.models.test_case import TestCase


class FakeDatasetsApi:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def list(self, *, page: int, limit: int) -> Any:
        self.calls += 1
        if self.fail:
            raise TimeoutError("upstream unavailable")
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    id="dataset-1",
                    name="conversation-one",
                    description="demo",
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    metadata={},
                )
            ]
        )


class FakeDatasetItemsApi:
    def __init__(self) -> None:
        self.list_calls = 0
        self.total = 2
        self.deleted: list[str] = []

    def list(
        self,
        *,
        dataset_name: str,
        version: datetime | None,
        page: int,
        limit: int,
    ) -> Any:
        self.list_calls += 1
        items = [make_item(f"item-{page}", dataset_name)]
        return SimpleNamespace(
            data=items,
            meta=SimpleNamespace(total_items=self.total),
        )

    def get(self, example_id: str) -> Any:
        return make_item(example_id, "conversation-one")

    def delete(self, example_id: str) -> None:
        self.deleted.append(example_id)


class FakeClient:
    def __init__(self) -> None:
        self._resources = object()
        self.datasets_api = FakeDatasetsApi()
        self.items_api = FakeDatasetItemsApi()
        self.api = SimpleNamespace(
            datasets=self.datasets_api,
            dataset_items=self.items_api,
        )
        self.created_items: list[dict[str, Any]] = []

    def create_dataset_item(self, **kwargs: Any) -> Any:
        self.created_items.append(kwargs)
        return SimpleNamespace(id=kwargs["id"])


def make_item(item_id: str, dataset_name: str) -> Any:
    return SimpleNamespace(
        id=item_id,
        dataset_name=dataset_name,
        input={"messages": [{"role": "user", "content": item_id}]},
        expected_output={},
        metadata={"name": item_id, "source": "manual"},
    )


def make_case(case_id: str = "new-item") -> TestCase:
    return TestCase(
        id=case_id,
        dataset_version="conversation-one",
        name=case_id,
        input_messages=[{"role": "user", "content": "hello"}],
    )


@pytest.fixture(autouse=True)
def clear_provider_caches() -> None:
    provider_module._list_cache.clear()
    provider_module._snapshot_cache.clear()
    provider_module._page_cache.clear()
    provider_module._count_cache.clear()
    provider_module._list_generation.clear()
    provider_module._dataset_generation.clear()
    provider_module._dataset_scope_generation.clear()
    provider_module._inflight.clear()


@pytest.mark.asyncio
async def test_dataset_list_cache_is_shared_across_provider_instances() -> None:
    client = FakeClient()
    first = LangfuseDatasetProvider(client)
    second = LangfuseDatasetProvider(client)

    assert [item.name for item in await first.list_datasets()] == ["conversation-one"]
    assert [item.name for item in await second.list_datasets()] == ["conversation-one"]
    assert client.datasets_api.calls == 1


@pytest.mark.asyncio
async def test_expired_list_returns_stale_while_failed_refresh_runs() -> None:
    client = FakeClient()
    provider = LangfuseDatasetProvider(client)
    await provider.list_datasets()

    entry = provider_module._list_cache[provider._scope]
    entry.stored_at -= provider_module._LIST_CACHE_TTL_S + 1
    client.datasets_api.fail = True

    result = await provider.list_datasets()
    assert [item.name for item in result] == ["conversation-one"]

    await asyncio.sleep(0.05)
    assert client.datasets_api.calls == 2
    assert provider_module._list_cache[provider._scope] is entry


@pytest.mark.asyncio
async def test_page_read_uses_upstream_pagination_and_populates_count() -> None:
    client = FakeClient()
    provider = LangfuseDatasetProvider(client)

    first_items, first_total = await provider.load_cases_page(
        "conversation-one", page=2, page_size=20
    )
    second_items, second_total = await provider.load_cases_page(
        "conversation-one", page=2, page_size=20
    )

    assert [item.id for item in first_items] == ["item-2"]
    assert [item.id for item in second_items] == ["item-2"]
    assert first_total == second_total == 2
    assert client.items_api.list_calls == 1

    datasets = await provider.list_datasets()
    assert datasets[0].example_count == 2


@pytest.mark.asyncio
async def test_historical_page_does_not_replace_current_count() -> None:
    client = FakeClient()
    provider = LangfuseDatasetProvider(client)

    client.items_api.total = 2
    await provider.load_cases_page("conversation-one", page=1, page_size=1)
    client.items_api.total = 99
    await provider.load_cases_page(
        "conversation-one",
        page=1,
        page_size=1,
        as_of=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )

    datasets = await provider.list_datasets()
    assert datasets[0].example_count == 2


@pytest.mark.asyncio
async def test_successful_write_invalidates_dataset_page_cache() -> None:
    client = FakeClient()
    provider = LangfuseDatasetProvider(client)

    await provider.load_cases_page("conversation-one", page=1, page_size=20)
    assert client.items_api.list_calls == 1

    await provider.add_case("conversation-one", make_case())
    await provider.load_cases_page("conversation-one", page=1, page_size=20)

    assert client.items_api.list_calls == 2
    assert client.created_items[0]["dataset_name"] == "conversation-one"


@pytest.mark.asyncio
async def test_read_started_before_write_cannot_refill_invalidated_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    provider = LangfuseDatasetProvider(client)
    original_to_thread = provider_module.to_thread
    entered = asyncio.Event()
    release = asyncio.Event()

    async def controlled_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        if func == client.items_api.list:
            entered.set()
            await release.wait()
            return func(*args, **kwargs)
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(provider_module, "to_thread", controlled_to_thread)
    read_task = asyncio.create_task(
        provider.load_cases_page("conversation-one", page=1, page_size=20)
    )
    await entered.wait()

    provider._invalidate_dataset("conversation-one")
    release.set()
    items, total = await read_task

    assert [item.id for item in items] == ["item-1"]
    assert total == 2
    assert not any(
        key[0] == provider._scope and key[1] == "conversation-one"
        for key in provider_module._page_cache
    )
    assert (provider._scope, "conversation-one") not in provider_module._count_cache
