from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db import async_session_factory
from agent_eval.governance.audit import AuditService


async def get_audit_session():
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def log_audit(
    entity_type: str,
    entity_id: str,
    action: str,
    user_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    async with async_session_factory() as session:
        try:
            audit = AuditService(session)
            await audit.log(
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                user_id=user_id,
                details=details,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
