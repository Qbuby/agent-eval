from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db_models.tables import OmniAgentEventRow


def event_dict(row: OmniAgentEventRow) -> dict[str, Any]:
    return {
        "cursor": row.id,
        "type": row.event_type,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "session_id": str(row.session_id) if row.session_id else None,
        "message_id": str(row.message_id) if row.message_id else None,
        "payload": row.payload or {},
        "created_at": row.created_at,
    }


async def append_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    event_type: str,
    payload: dict[str, Any] | None = None,
    session_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> OmniAgentEventRow:
    row = OmniAgentEventRow(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        message_id=message_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload or {},
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    return row


async def list_events(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    after: int = 0,
    session_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[OmniAgentEventRow]:
    owner = (
        OmniAgentEventRow.user_id.is_(None)
        if user_id is None
        else OmniAgentEventRow.user_id == user_id
    )
    stmt = select(OmniAgentEventRow).where(
        OmniAgentEventRow.tenant_id == tenant_id,
        owner,
        OmniAgentEventRow.id > max(0, after),
    )
    if session_id is not None:
        stmt = stmt.where(OmniAgentEventRow.session_id == session_id)
    rows = await db.execute(stmt.order_by(OmniAgentEventRow.id).limit(min(limit, 200)))
    return list(rows.scalars().all())
