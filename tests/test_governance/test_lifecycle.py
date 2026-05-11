from __future__ import annotations

import pytest

from agent_eval.governance.lifecycle import (
    DatasetStatus,
    LifecycleConfig,
    LifecycleService,
    RetentionPolicy,
)


class TestLifecycleConfig:
    def test_default_config(self):
        config = LifecycleConfig()
        assert config.max_examples == 10000
        assert config.retention_policy == RetentionPolicy.FIFO
        assert config.capacity_warning_threshold == 0.9

    def test_custom_config(self):
        config = LifecycleConfig(
            max_examples=500,
            retention_policy=RetentionPolicy.SCORE,
            capacity_warning_threshold=0.8,
        )
        assert config.max_examples == 500
        assert config.retention_policy == RetentionPolicy.SCORE


class TestDatasetStatus:
    def test_status_values(self):
        assert DatasetStatus.ACTIVE == "active"
        assert DatasetStatus.ARCHIVED == "archived"
        assert DatasetStatus.DEPRECATED == "deprecated"


class TestRetentionPolicy:
    def test_policy_values(self):
        assert RetentionPolicy.FIFO == "fifo"
        assert RetentionPolicy.SCORE == "score"


class TestLifecycleServiceScorePolicy:
    @pytest.mark.asyncio
    async def test_score_policy_raises_not_implemented(self):
        from unittest.mock import AsyncMock, MagicMock

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 200
        session.execute = AsyncMock(return_value=mock_result)

        service = LifecycleService(session)
        config = LifecycleConfig(
            max_examples=100,
            retention_policy=RetentionPolicy.SCORE,
        )

        with pytest.raises(NotImplementedError, match="Score-based retention"):
            await service.enforce_retention("test-dataset", config)


class TestLifecycleServiceCapacity:
    @pytest.mark.asyncio
    async def test_check_capacity_no_warning(self):
        from unittest.mock import AsyncMock, MagicMock

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 50
        session.execute = AsyncMock(return_value=mock_result)

        service = LifecycleService(session)
        config = LifecycleConfig(max_examples=1000)

        capacity = await service.check_capacity("test-dataset", config)
        assert capacity.current_count == 50
        assert capacity.max_count == 1000
        assert capacity.usage_ratio == 0.05
        assert capacity.warning is False

    @pytest.mark.asyncio
    async def test_check_capacity_with_warning(self):
        from unittest.mock import AsyncMock, MagicMock

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 950
        session.execute = AsyncMock(return_value=mock_result)

        service = LifecycleService(session)
        config = LifecycleConfig(max_examples=1000, capacity_warning_threshold=0.9)

        capacity = await service.check_capacity("test-dataset", config)
        assert capacity.current_count == 950
        assert capacity.usage_ratio == 0.95
        assert capacity.warning is True
