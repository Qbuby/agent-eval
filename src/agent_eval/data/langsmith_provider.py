from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from langsmith import Client
from langsmith.schemas import DataType

from agent_eval.data import converter
from agent_eval.data._utils import to_thread
from agent_eval.data.provider import DatasetInfo, VersionInfo
from agent_eval.models.test_case import TestCase

logger = logging.getLogger(__name__)


class LangSmithDatasetProvider:

    def __init__(self, client: Client | None = None, **client_kwargs: Any):
        self.client = client or Client(**client_kwargs)

    # ---- Dataset CRUD ----

    async def create_dataset(
        self, name: str, description: str = "", metadata: dict | None = None
    ) -> str:
        ds = await to_thread(
            self.client.create_dataset,
            dataset_name=name,
            description=description,
            data_type=DataType.kv,
            metadata=metadata or {},
        )
        logger.info("Created dataset '%s' (id=%s)", name, ds.id)
        return str(ds.id)

    async def list_datasets(self, name_contains: str | None = None) -> list[DatasetInfo]:
        kwargs: dict[str, Any] = {}
        if name_contains:
            kwargs["dataset_name_contains"] = name_contains
        datasets = await to_thread(self.client.list_datasets, **kwargs)
        return [
            DatasetInfo(
                id=str(ds.id),
                name=ds.name,
                description=ds.description or "",
                example_count=ds.example_count or 0,
                created_at=ds.created_at,
                metadata=ds.metadata or {},
            )
            for ds in datasets
        ]

    async def get_dataset(self, name: str) -> DatasetInfo:
        ds = await to_thread(self.client.read_dataset, dataset_name=name)
        return DatasetInfo(
            id=str(ds.id),
            name=ds.name,
            description=ds.description or "",
            example_count=ds.example_count or 0,
            created_at=ds.created_at,
            metadata=ds.metadata or {},
        )

    async def delete_dataset(self, name: str) -> None:
        ds = await to_thread(self.client.read_dataset, dataset_name=name)
        await to_thread(self.client.delete_dataset, dataset_id=ds.id)
        logger.info("Deleted dataset '%s'", name)

    # ---- Example / TestCase CRUD ----

    async def add_case(
        self, dataset_name: str, case: TestCase, split: str | None = None
    ) -> str:
        params = converter.case_to_example(case, split=split)
        example = await to_thread(
            self.client.create_example,
            inputs=params["inputs"],
            outputs=params["outputs"],
            dataset_name=dataset_name,
            metadata=params["metadata"],
            split=params.get("split"),
        )
        return str(example.id)

    async def add_cases_batch(
        self,
        dataset_name: str,
        cases: list[TestCase],
        split: str | None = None,
        source_run_ids: list[str] | None = None,
    ) -> list[str]:
        ds = await to_thread(self.client.read_dataset, dataset_name=dataset_name)
        all_params = [converter.case_to_example(c, split=split) for c in cases]

        batch_size = 20
        if len(all_params) <= batch_size:
            return await self._create_batch(ds.id, all_params, split, source_run_ids)

        all_ids: list[str] = []
        batches = [all_params[i:i + batch_size] for i in range(0, len(all_params), batch_size)]
        src_batches = None
        if source_run_ids:
            src_batches = [source_run_ids[i:i + batch_size] for i in range(0, len(source_run_ids), batch_size)]

        async def _do_batch(idx: int) -> list[str]:
            src = src_batches[idx] if src_batches else None
            return await self._create_batch(ds.id, batches[idx], split, src)

        results = await asyncio.gather(*[_do_batch(i) for i in range(len(batches))])
        for ids in results:
            all_ids.extend(ids)
        return all_ids

    async def _create_batch(
        self,
        dataset_id: Any,
        params: list[dict[str, Any]],
        split: str | None,
        source_run_ids: list[str] | None,
    ) -> list[str]:
        kwargs: dict[str, Any] = {
            "inputs": [p["inputs"] for p in params],
            "outputs": [p["outputs"] for p in params],
            "metadata": [p["metadata"] for p in params],
            "dataset_id": dataset_id,
        }
        if split:
            kwargs["splits"] = [p.get("split") for p in params]
        if source_run_ids:
            kwargs["source_run_ids"] = source_run_ids

        created = await to_thread(self.client.create_examples, **kwargs)
        if created is None:
            return []
        return [str(getattr(e, "id", e)) for e in created]

    async def load_cases(
        self,
        dataset_name: str,
        *,
        as_of: datetime | None = None,
        splits: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> list[TestCase]:
        kwargs: dict[str, Any] = {"dataset_name": dataset_name}
        if as_of:
            kwargs["as_of"] = as_of
        if splits:
            kwargs["splits"] = splits
        if limit:
            kwargs["limit"] = limit

        examples = await to_thread(self.client.list_examples, **kwargs)
        cases = [converter.example_to_test_case(ex) for ex in examples]

        if tags:
            tag_set = set(tags)
            cases = [c for c in cases if tag_set.intersection(c.tags)]

        return cases

    async def update_case(self, example_id: str, case: TestCase) -> None:
        params = converter.case_to_example(case)
        await to_thread(
            self.client.update_example,
            example_id=example_id,
            inputs=params["inputs"],
            outputs=params["outputs"],
            metadata=params["metadata"],
        )

    async def delete_case(self, example_id: str) -> None:
        await to_thread(self.client.delete_example, example_id=example_id)

    async def delete_cases_batch(self, example_ids: list[str]) -> None:
        await to_thread(self.client.delete_examples, example_ids=example_ids)

    # ---- Versioning ----

    async def list_versions(self, dataset_name: str) -> list[VersionInfo]:
        ds = await to_thread(self.client.read_dataset, dataset_name=dataset_name)
        versions = await to_thread(self.client.list_dataset_versions, dataset_id=ds.id)
        return [
            VersionInfo(version_id=str(v.as_of), created_at=v.as_of)
            for v in versions
        ]

    # ---- Pull external dataset ----

    async def pull_external_dataset(
        self,
        source_dataset_name: str,
        *,
        limit: int | None = None,
    ) -> list[TestCase]:
        """Fetch examples from an external LangSmith dataset and convert them
        to TestCase objects.  Automatically detects whether each example was
        created by this system (native) or externally, and applies the
        appropriate converter."""
        kwargs: dict[str, Any] = {"dataset_name": source_dataset_name}
        if limit:
            kwargs["limit"] = limit

        examples = list(await to_thread(self.client.list_examples, **kwargs))
        if not examples:
            return []

        cases: list[TestCase] = []
        for ex in examples:
            if converter.is_native_example(ex):
                cases.append(converter.example_to_test_case(ex))
            else:
                cases.append(
                    converter.external_example_to_test_case(ex, dataset_name=source_dataset_name)
                )

        logger.info(
            "Pulled %d example(s) from external dataset '%s'",
            len(cases), source_dataset_name,
        )
        return cases
