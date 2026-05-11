from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db_models.tables import ExampleFingerprintRow


class DedupStrategy(str, Enum):
    SKIP = "skip"
    REPLACE = "replace"
    APPEND_SUFFIX = "append_suffix"


@dataclass
class DedupResult:
    total: int = 0
    skipped: int = 0
    replaced: int = 0
    suffixed: int = 0
    passed: int = 0
    duplicates: list[dict[str, Any]] = field(default_factory=list)


class DedupService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def compute_fingerprint(input_messages: list[dict[str, str]]) -> str:
        normalized = json.dumps(input_messages, sort_keys=True, ensure_ascii=False)
        normalized = unicodedata.normalize("NFC", normalized).strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    async def check_duplicate(self, dataset_name: str, fingerprint: str) -> ExampleFingerprintRow | None:
        stmt = select(ExampleFingerprintRow).where(
            ExampleFingerprintRow.dataset_name == dataset_name,
            ExampleFingerprintRow.fingerprint == fingerprint,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def register_fingerprint(
        self, dataset_name: str, example_id: str, fingerprint: str
    ) -> ExampleFingerprintRow:
        row = ExampleFingerprintRow(
            dataset_name=dataset_name,
            example_id=example_id,
            fingerprint=fingerprint,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def deduplicate_batch(
        self,
        dataset_name: str,
        examples: list[dict[str, Any]],
        strategy: DedupStrategy = DedupStrategy.SKIP,
    ) -> DedupResult:
        result = DedupResult(total=len(examples))
        passed_examples: list[dict[str, Any]] = []

        for example in examples:
            input_messages = example.get("input_messages", [])
            fingerprint = self.compute_fingerprint(input_messages)
            existing = await self.check_duplicate(dataset_name, fingerprint)

            if existing is None:
                passed_examples.append(example)
                result.passed += 1
            else:
                dup_info = {
                    "example": example.get("name", ""),
                    "existing_id": existing.example_id,
                    "fingerprint": fingerprint,
                }

                if strategy == DedupStrategy.SKIP:
                    result.skipped += 1
                    dup_info["action"] = "skipped"
                elif strategy == DedupStrategy.REPLACE:
                    result.replaced += 1
                    dup_info["action"] = "replaced"
                    passed_examples.append(example)
                    existing.example_id = example.get("id", existing.example_id)
                elif strategy == DedupStrategy.APPEND_SUFFIX:
                    result.suffixed += 1
                    dup_info["action"] = "suffixed"
                    name = example.get("name", "")
                    example["name"] = f"{name}_dup_{uuid.uuid4().hex[:6]}"
                    passed_examples.append(example)

                result.duplicates.append(dup_info)

        for ex in passed_examples:
            fp = self.compute_fingerprint(ex.get("input_messages", []))
            existing_fp = await self.check_duplicate(dataset_name, fp)
            if existing_fp is None:
                await self.register_fingerprint(
                    dataset_name=dataset_name,
                    example_id=ex.get("id", str(uuid.uuid4())),
                    fingerprint=fp,
                )

        return result

    async def find_duplicates(self, dataset_name: str) -> list[dict[str, Any]]:
        stmt = select(ExampleFingerprintRow).where(
            ExampleFingerprintRow.dataset_name == dataset_name
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())

        fingerprint_groups: dict[str, list[ExampleFingerprintRow]] = {}
        for row in rows:
            fingerprint_groups.setdefault(row.fingerprint, []).append(row)

        duplicates = []
        for fp, group in fingerprint_groups.items():
            if len(group) > 1:
                duplicates.append({
                    "fingerprint": fp,
                    "count": len(group),
                    "example_ids": [r.example_id for r in group],
                })

        return duplicates

    async def remove_duplicates(self, dataset_name: str) -> list[str]:
        stmt = select(ExampleFingerprintRow).where(
            ExampleFingerprintRow.dataset_name == dataset_name
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())

        fingerprint_groups: dict[str, list[ExampleFingerprintRow]] = {}
        for row in rows:
            fingerprint_groups.setdefault(row.fingerprint, []).append(row)

        removed_ids: list[str] = []
        for fp, group in fingerprint_groups.items():
            if len(group) > 1:
                for dup in group[1:]:
                    removed_ids.append(dup.example_id)
                    await self.session.delete(dup)

        await self.session.flush()
        return removed_ids
