from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db_models.tables import AuditLogRow


class AuditService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def log(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        user_id: uuid.UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditLogRow:
        row = AuditLogRow(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            user_id=user_id,
            details=details,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def query(
        self,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
        user_id: uuid.UUID | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLogRow]:
        stmt = select(AuditLogRow)

        if entity_type is not None:
            stmt = stmt.where(AuditLogRow.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(AuditLogRow.entity_id == entity_id)
        if action is not None:
            stmt = stmt.where(AuditLogRow.action == action)
        if user_id is not None:
            stmt = stmt.where(AuditLogRow.user_id == user_id)
        if since is not None:
            stmt = stmt.where(AuditLogRow.created_at >= since)

        stmt = stmt.order_by(AuditLogRow.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
    ) -> int:
        from sqlalchemy import func

        stmt = select(func.count(AuditLogRow.id))
        if entity_type is not None:
            stmt = stmt.where(AuditLogRow.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(AuditLogRow.entity_id == entity_id)
        if action is not None:
            stmt = stmt.where(AuditLogRow.action == action)

        result = await self.session.execute(stmt)
        return result.scalar_one()
