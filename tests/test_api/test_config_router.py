from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("AUTH_SECRET_KEY", "test-secret-key")

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from agent_eval.api.routers.config import router
from agent_eval.auth.dependencies import get_current_user, require_admin


def _mock_admin():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = "admin"
    user.is_active = True
    user.username = "admin"
    return user


def _mock_user():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = "user"
    user.is_active = True
    user.username = "testuser"
    return user


def _mock_config_row(key="langsmith.api_url", value=None, category="langsmith"):
    if value is None:
        value = {"v": "https://api.smith.langchain.com"}
    row = MagicMock()
    row.key = key
    row.value = value
    row.category = category
    row.description = "test description"
    row.updated_by = None
    row.updated_at = datetime.now(timezone.utc)
    return row


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_current_user] = lambda: _mock_user()
    application.dependency_overrides[require_admin] = lambda: _mock_admin()
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestListConfigs:
    @pytest.mark.asyncio
    async def test_list_filters_sensitive(self, client):
        rows = [
            _mock_config_row("langsmith.api_url"),
            _mock_config_row("auth.secret_key", {"v": "secret"}, "general"),
        ]

        with patch("agent_eval.api.routers.config.config_service") as mock_svc:
            mock_svc.list = AsyncMock(return_value=rows)
            response = await client.get("/api/config")

        assert response.status_code == 200
        data = response.json()
        keys = [item["key"] for item in data]
        assert "langsmith.api_url" in keys
        assert "auth.secret_key" not in keys


class TestGetConfig:
    @pytest.mark.asyncio
    async def test_sensitive_key_forbidden(self, client):
        response = await client.get("/api/config/auth.secret_key")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_not_found(self, client):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("agent_eval.db.async_session_factory", return_value=mock_session):
            with patch("agent_eval.api.routers.config.config_service") as mock_svc:
                mock_svc.get = AsyncMock(return_value=None)
                response = await client.get("/api/config/nonexistent.key")

        assert response.status_code == 404


class TestUpdateConfig:
    @pytest.mark.asyncio
    async def test_sensitive_key_forbidden(self, client):
        response = await client.put(
            "/api/config/auth.secret_key",
            json={"value": "hacked"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_update_success(self, client, app):
        admin = _mock_admin()
        app.dependency_overrides[require_admin] = lambda: admin
        row = _mock_config_row("scheduler.poll_interval_seconds", {"v": 120}, "scheduler")

        with patch("agent_eval.api.routers.config.config_service") as mock_svc:
            mock_svc.set = AsyncMock(return_value=row)
            response = await client.put(
                "/api/config/scheduler.poll_interval_seconds",
                json={"value": 120, "description": "updated"},
            )

        assert response.status_code == 200
        mock_svc.set.assert_called_once_with(
            "scheduler.poll_interval_seconds", 120, user_id=admin.id, description="updated"
        )


class TestDeleteConfig:
    @pytest.mark.asyncio
    async def test_sensitive_key_forbidden(self, client):
        response = await client.delete("/api/config/db.password")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_not_found(self, client):
        with patch("agent_eval.api.routers.config.config_service") as mock_svc:
            mock_svc.delete = AsyncMock(return_value=False)
            response = await client.delete("/api/config/nonexistent.key")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_success(self, client):
        with patch("agent_eval.api.routers.config.config_service") as mock_svc:
            mock_svc.delete = AsyncMock(return_value=True)
            response = await client.delete("/api/config/scheduler.enabled")

        assert response.status_code == 200
        assert response.json()["key"] == "scheduler.enabled"


class TestBatchUpdate:
    @pytest.mark.asyncio
    async def test_sensitive_key_in_batch_forbidden(self, client):
        response = await client.post(
            "/api/config/batch",
            json={"items": {"auth.secret_key": "hacked", "scheduler.enabled": True}},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_batch_success(self, client, app):
        admin = _mock_admin()
        app.dependency_overrides[require_admin] = lambda: admin
        rows = [
            _mock_config_row("scheduler.enabled", {"v": True}, "scheduler"),
            _mock_config_row("scheduler.poll_interval_seconds", {"v": 30}, "scheduler"),
        ]

        with patch("agent_eval.api.routers.config.config_service") as mock_svc:
            mock_svc.batch_set = AsyncMock(return_value=rows)
            response = await client.post(
                "/api/config/batch",
                json={"items": {"scheduler.enabled": True, "scheduler.poll_interval_seconds": 30}},
            )

        assert response.status_code == 200
        assert len(response.json()) == 2
