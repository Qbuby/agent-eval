from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from agent_eval.models.test_case import TestCase


@dataclass
class DatasetInfo:
    id: str
    name: str
    description: str
    example_count: int
    created_at: datetime
    metadata: dict = field(default_factory=dict)


@dataclass
class VersionInfo:
    version_id: str
    created_at: datetime
    example_count: int | None = None


@runtime_checkable
class DatasetProvider(Protocol):

    async def create_dataset(
        self, name: str, description: str = "", metadata: dict | None = None
    ) -> str: ...

    async def list_datasets(self, name_contains: str | None = None) -> list[DatasetInfo]: ...

    async def get_dataset(self, name: str) -> DatasetInfo: ...

    async def delete_dataset(self, name: str) -> None: ...

    async def add_case(
        self, dataset_name: str, case: TestCase, split: str | None = None
    ) -> str: ...

    async def add_cases_batch(
        self,
        dataset_name: str,
        cases: list[TestCase],
        split: str | None = None,
        source_run_ids: list[str] | None = None,
    ) -> list[str]: ...

    async def load_cases(
        self,
        dataset_name: str,
        *,
        as_of: datetime | None = None,
        splits: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> list[TestCase]: ...

    async def update_case(self, example_id: str, case: TestCase) -> None: ...

    async def delete_case(self, example_id: str) -> None: ...

    async def delete_cases_batch(self, example_ids: list[str]) -> None: ...

    async def list_versions(self, dataset_name: str) -> list[VersionInfo]: ...

    async def pull_external_dataset(
        self, source_dataset_name: str, *, limit: int | None = None
    ) -> list[TestCase]: ...
