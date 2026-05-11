from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_eval.governance.audit import AuditService


class TestAuditService:
    def setup_method(self):
        self.session = AsyncMock()
        self.session.add = MagicMock()
        self.session.flush = AsyncMock()
        self.service = AuditService(self.session)

    @pytest.mark.asyncio
    async def test_log_creates_audit_entry(self):
        result = await self.service.log(
            entity_type="dataset",
            entity_id="test-dataset",
            action="create",
            user_id=uuid.uuid4(),
            details={"key": "value"},
        )
        self.session.add.assert_called_once()
        self.session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_log_without_user_id(self):
        result = await self.service.log(
            entity_type="example",
            entity_id="ex-123",
            action="delete",
        )
        self.session.add.assert_called_once()
        added_row = self.session.add.call_args[0][0]
        assert added_row.user_id is None

    @pytest.mark.asyncio
    async def test_log_stores_correct_fields(self):
        user_id = uuid.uuid4()
        await self.service.log(
            entity_type="rule",
            entity_id="rule-456",
            action="update",
            user_id=user_id,
            details={"changed": "priority"},
        )
        added_row = self.session.add.call_args[0][0]
        assert added_row.entity_type == "rule"
        assert added_row.entity_id == "rule-456"
        assert added_row.action == "update"
        assert added_row.user_id == user_id
        assert added_row.details == {"changed": "priority"}

    @pytest.mark.asyncio
    async def test_query_builds_correct_filters(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        self.session.execute = AsyncMock(return_value=mock_result)

        logs = await self.service.query(
            entity_type="dataset",
            action="create",
            limit=10,
        )
        assert logs == []
        self.session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_count_returns_integer(self):
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 5
        self.session.execute = AsyncMock(return_value=mock_result)

        count = await self.service.count(entity_type="dataset")
        assert count == 5
