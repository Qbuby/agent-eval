from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from agent_eval.data.provider import DatasetInfo, DatasetProvider, VersionInfo
from agent_eval.models.test_case import TestCase


@dataclass
class DatasetStats:
    total_cases: int
    by_source: dict[str, int] = field(default_factory=dict)
    by_tag: dict[str, int] = field(default_factory=dict)
    has_expected_output: int = 0
    has_criteria: int = 0
    has_tool_calls: int = 0
    avg_messages_per_case: float = 0.0


class DatasetManager:
    """Facade that delegates to a DatasetProvider implementation."""

    def __init__(self, provider: DatasetProvider):
        self.provider = provider

    async def create_dataset(
        self, name: str, description: str = "", metadata: dict | None = None
    ) -> str:
        return await self.provider.create_dataset(name, description, metadata)

    async def list_datasets(self, name_contains: str | None = None) -> list[DatasetInfo]:
        return await self.provider.list_datasets(name_contains)

    async def get_dataset(self, name: str) -> DatasetInfo:
        return await self.provider.get_dataset(name)

    async def delete_dataset(self, name: str) -> None:
        await self.provider.delete_dataset(name)

    async def add_case(
        self, dataset_name: str, case: TestCase, split: str | None = None
    ) -> str:
        return await self.provider.add_case(dataset_name, case, split=split)

    async def add_cases_batch(
        self,
        dataset_name: str,
        cases: list[TestCase],
        split: str | None = None,
        source_run_ids: list[str] | None = None,
    ) -> list[str]:
        return await self.provider.add_cases_batch(
            dataset_name, cases, split=split, source_run_ids=source_run_ids
        )

    async def load_cases(
        self,
        dataset_name: str,
        *,
        as_of: datetime | None = None,
        splits: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> list[TestCase]:
        return await self.provider.load_cases(
            dataset_name, as_of=as_of, splits=splits, tags=tags, limit=limit
        )

    async def get_case(self, example_id: str) -> TestCase:
        return await self.provider.get_case(example_id)

    async def update_case(self, example_id: str, case: TestCase) -> None:
        await self.provider.update_case(example_id, case)

    async def delete_case(self, example_id: str) -> None:
        await self.provider.delete_case(example_id)

    async def delete_cases_batch(self, example_ids: list[str]) -> None:
        await self.provider.delete_cases_batch(example_ids)

    async def list_versions(self, dataset_name: str) -> list[VersionInfo]:
        return await self.provider.list_versions(dataset_name)

    async def pull_external_dataset(
        self,
        source_dataset_name: str,
        *,
        target_dataset_name: str | None = None,
        split: str | None = None,
        limit: int | None = None,
    ) -> list[TestCase]:
        cases = await self.provider.pull_external_dataset(
            source_dataset_name, limit=limit
        )
        if target_dataset_name and cases:
            await self.provider.add_cases_batch(target_dataset_name, cases, split=split)
        return cases

    async def export_cases(
        self,
        dataset_name: str,
        *,
        as_of: datetime | None = None,
        splits: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        cases = await self.load_cases(
            dataset_name, as_of=as_of, splits=splits, tags=tags
        )
        return [case.model_dump(mode="json", exclude_none=True) for case in cases]

    async def get_stats(
        self,
        dataset_name: str,
        *,
        splits: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> DatasetStats:
        cases = await self.load_cases(dataset_name, splits=splits, tags=tags)
        source_counter: Counter[str] = Counter()
        tag_counter: Counter[str] = Counter()
        has_output = 0
        has_criteria = 0
        has_tools = 0
        total_messages = 0

        for case in cases:
            source_counter[case.source] += 1
            for t in case.tags:
                tag_counter[t] += 1
            if case.expected_output:
                has_output += 1
            if case.expected_output_criteria:
                has_criteria += 1
            if case.expected_tool_calls:
                has_tools += 1
            total_messages += len(case.input_messages)

        return DatasetStats(
            total_cases=len(cases),
            by_source=dict(source_counter),
            by_tag=dict(tag_counter.most_common(20)),
            has_expected_output=has_output,
            has_criteria=has_criteria,
            has_tool_calls=has_tools,
            avg_messages_per_case=total_messages / len(cases) if cases else 0,
        )
