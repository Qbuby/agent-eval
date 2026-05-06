from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("AUTH_SECRET_KEY", "test-secret-key")

import pytest

from agent_eval.config_service import ConfigService


@pytest.fixture
def service():
    return ConfigService(cache_ttl=5.0)


class TestIsSensitive:
    def test_auth_prefix(self):
        assert ConfigService.is_sensitive("auth.secret_key") is True

    def test_llm_api_key(self):
        assert ConfigService.is_sensitive("llm.api_key") is True

    def test_db_prefix(self):
        assert ConfigService.is_sensitive("db.password") is True

    def test_non_sensitive(self):
        assert ConfigService.is_sensitive("langsmith.api_url") is False
        assert ConfigService.is_sensitive("scheduler.enabled") is False


class TestGetEnvFallback:
    def test_sensitive_key_returns_none(self, service):
        result = service._get_env_fallback("auth.secret_key")
        assert result is None

    def test_non_sensitive_langsmith_key(self, service):
        result = service._get_env_fallback("langsmith.api_url")
        assert result is not None or result == ""

    def test_unknown_section_returns_none(self, service):
        result = service._get_env_fallback("unknown.field")
        assert result is None

    def test_no_dot_returns_none(self, service):
        result = service._get_env_fallback("nodot")
        assert result is None


class TestGet:
    @pytest.mark.asyncio
    async def test_sensitive_key_returns_none(self, service):
        result = await service.get("auth.secret_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_sensitive_db_key_returns_none(self, service):
        result = await service.get("db.password")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit(self, service):
        import time
        service._cache["test.key"] = ("cached_value", time.time())
        result = await service.get("test.key")
        assert result == "cached_value"

    @pytest.mark.asyncio
    async def test_cache_expired(self, service):
        import time
        service._cache["test.key"] = ("old_value", time.time() - 100)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agent_eval.config_service.async_session_factory", return_value=mock_session):
            result = await service.get("test.key")
        assert result is None

    @pytest.mark.asyncio
    async def test_db_value_returned_and_cached(self, service):
        mock_row = MagicMock()
        mock_row.value = {"v": "db_value"}

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agent_eval.config_service.async_session_factory", return_value=mock_session):
            result = await service.get("langsmith.api_url")

        assert result == "db_value"
        assert "langsmith.api_url" in service._cache
        assert service._cache["langsmith.api_url"][0] == "db_value"


class TestSet:
    @pytest.mark.asyncio
    async def test_set_new_key(self, service):
        mock_row = MagicMock()
        mock_row.key = "scheduler.poll_interval_seconds"
        mock_row.value = {"v": 120}
        mock_row.category = "scheduler"
        mock_row.description = "test"
        mock_row.updated_by = None
        mock_row.updated_at = datetime.now(timezone.utc)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agent_eval.config_service.async_session_factory", return_value=mock_session):
            await service.set("scheduler.poll_interval_seconds", 120, description="test")

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        assert "scheduler.poll_interval_seconds" in service._cache

    @pytest.mark.asyncio
    async def test_set_existing_key_updates(self, service):
        existing_row = MagicMock()
        existing_row.key = "scheduler.enabled"
        existing_row.value = {"v": True}
        existing_row.category = "scheduler"
        existing_row.description = None
        existing_row.updated_by = None
        existing_row.updated_at = datetime.now(timezone.utc)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_row
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        user_id = uuid.uuid4()
        with patch("agent_eval.config_service.async_session_factory", return_value=mock_session):
            await service.set("scheduler.enabled", False, user_id=user_id, description="disabled")

        assert existing_row.value == {"v": False}
        assert existing_row.updated_by == user_id
        assert existing_row.description == "disabled"

    @pytest.mark.asyncio
    async def test_set_triggers_listener(self, service):
        listener = MagicMock()
        service.on_change(listener)

        mock_row = MagicMock()
        mock_row.key = "test.key"
        mock_row.value = {"v": "new"}

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agent_eval.config_service.async_session_factory", return_value=mock_session):
            await service.set("test.key", "new")

        listener.assert_called_once_with("test.key", "new")


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self, service):
        import time
        service._cache["test.key"] = ("val", time.time())

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agent_eval.config_service.async_session_factory", return_value=mock_session):
            result = await service.delete("test.key")

        assert result is True
        assert "test.key" not in service._cache

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, service):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agent_eval.config_service.async_session_factory", return_value=mock_session):
            result = await service.delete("nonexistent.key")

        assert result is False


class TestBatchSet:
    @pytest.mark.asyncio
    async def test_batch_set_single_transaction(self, service):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agent_eval.config_service.async_session_factory", return_value=mock_session):
            results = await service.batch_set({"a.key": 1, "b.key": 2})

        assert len(results) == 2
        mock_session.commit.assert_called_once()


class TestInferCategory:
    def test_langsmith(self):
        assert ConfigService._infer_category("langsmith.api_url") == "langsmith"

    def test_scheduler(self):
        assert ConfigService._infer_category("scheduler.enabled") == "scheduler"

    def test_routing(self):
        assert ConfigService._infer_category("routing.default_dataset") == "routing"

    def test_general(self):
        assert ConfigService._infer_category("custom.setting") == "general"
