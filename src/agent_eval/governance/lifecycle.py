from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db_models.tables import ExampleFingerprintRow


class DatasetStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DEPRECATED = "deprecated"


class RetentionPolicy(str, Enum):
    FIFO = "fifo"
    SCORE = "score"


@dataclass
class LifecycleConfig:
    max_examples: int = 10000
    retention_policy: RetentionPolicy = RetentionPolicy.FIFO
    capacity_warning_threshold: float = 0.9


@dataclass
class CapacityInfo:
    current_count: int
    max_count: int
    usage_ratio: float
    warning: bool


class LifecycleService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_dataset_example_count(self, dataset_name: str) -> int:
        stmt = select(func.count(ExampleFingerprintRow.id)).where(
            ExampleFingerprintRow.dataset_name == dataset_name
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def check_capacity(
        self, dataset_name: str, config: LifecycleConfig
    ) -> CapacityInfo:
        count = await self.get_dataset_example_count(dataset_name)
        usage_ratio = count / config.max_examples if config.max_examples > 0 else 0.0
        return CapacityInfo(
            current_count=count,
            max_count=config.max_examples,
            usage_ratio=usage_ratio,
            warning=usage_ratio >= config.capacity_warning_threshold,
        )

    async def get_oldest_fingerprints(
        self, dataset_name: str, limit: int
    ) -> list[ExampleFingerprintRow]:
        stmt = (
            select(ExampleFingerprintRow)
            .where(ExampleFingerprintRow.dataset_name == dataset_name)
            .order_by(ExampleFingerprintRow.created_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def enforce_retention(
        self, dataset_name: str, config: LifecycleConfig
    ) -> list[str]:
        count = await self.get_dataset_example_count(dataset_name)
        if count <= config.max_examples:
            return []

        excess = count - config.max_examples
        if config.retention_policy == RetentionPolicy.FIFO:
            oldest = await self.get_oldest_fingerprints(dataset_name, excess)
            removed_ids = [row.example_id for row in oldest]
            for row in oldest:
                await self.session.delete(row)
            await self.session.flush()
            return removed_ids

        if config.retention_policy == RetentionPolicy.SCORE:
            raise NotImplementedError("Score-based retention policy is not yet implemented")

        return []
