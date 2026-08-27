from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db_models.tables import OmniAgentMemoryRow
from agent_eval.omniagent_runtime.policy import DEFAULT_POLICY
from agent_eval.omniagent_runtime.security import canonical_digest


def memory_dict(row: OmniAgentMemoryRow) -> dict[str, Any]:
    return {"id": str(row.id), "title": row.title, "content": row.content,
            "tags": row.tags or [], "created_at": row.created_at, "updated_at": row.updated_at}


async def save_memory(db: AsyncSession, *, tenant_id: uuid.UUID, owner_id: uuid.UUID | None,
                      title: str, content: str, tags: list[str] | None = None,
                      source_action_id: uuid.UUID | None = None) -> OmniAgentMemoryRow:
    owner = OmniAgentMemoryRow.owner_id.is_(None) if owner_id is None else OmniAgentMemoryRow.owner_id == owner_id
    contents = list((await db.execute(select(OmniAgentMemoryRow.content).where(
        OmniAgentMemoryRow.tenant_id == tenant_id,
        owner,
        OmniAgentMemoryRow.deleted_at.is_(None),
    ))).scalars())
    size = sum(len(value.encode("utf-8")) for value in contents)
    encoded = content.encode("utf-8")
    if len(contents) >= DEFAULT_POLICY.memories_user or size + len(encoded) > DEFAULT_POLICY.memory_bytes_user:
        raise ValueError("QUOTA_EXCEEDED")
    row = OmniAgentMemoryRow(
        tenant_id=tenant_id,
        owner_id=owner_id,
        title=title[:256],
        content=content,
        tags=(tags or [])[:20],
        content_digest=canonical_digest(content),
        source_action_id=source_action_id,
    )
    db.add(row)
    await db.flush()
    return row


async def search_memories(db: AsyncSession, *, tenant_id: uuid.UUID, owner_id: uuid.UUID | None,
                          query: str = "", limit: int = 20) -> list[OmniAgentMemoryRow]:
    owner = OmniAgentMemoryRow.owner_id.is_(None) if owner_id is None else OmniAgentMemoryRow.owner_id == owner_id
    stmt = select(OmniAgentMemoryRow).where(OmniAgentMemoryRow.tenant_id == tenant_id, owner,
        OmniAgentMemoryRow.deleted_at.is_(None))
    if query.strip():
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(or_(OmniAgentMemoryRow.title.ilike(pattern), OmniAgentMemoryRow.content.ilike(pattern)))
    return list((await db.execute(stmt.order_by(OmniAgentMemoryRow.updated_at.desc()).limit(min(limit, 50)))).scalars())


async def delete_memory(db: AsyncSession, *, memory_id: uuid.UUID, tenant_id: uuid.UUID,
                        owner_id: uuid.UUID | None) -> bool:
    owner = OmniAgentMemoryRow.owner_id.is_(None) if owner_id is None else OmniAgentMemoryRow.owner_id == owner_id
    row = (
        await db.execute(
            select(OmniAgentMemoryRow)
            .where(
                OmniAgentMemoryRow.id == memory_id,
                OmniAgentMemoryRow.tenant_id == tenant_id,
                owner,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    row.deleted_at = datetime.now(timezone.utc)
    return True
