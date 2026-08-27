from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from agent_eval.auth.dependencies import require_internal
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import OmniAgentChatMessageRow, OmniAgentChatSessionRow, UserRow
from agent_eval.services.omniagent_chat import (
    begin_generation,
    get_owned_session,
    message_dict,
    owner_clause,
    owner_scope,
    recover_stale_generation,
    session_dict,
    stream_upstream,
    utcnow,
)

router = APIRouter(prefix="/api/omniagent", tags=["omniagent"])


class CreateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=256)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split()).strip()
        return value or None


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = " ".join(value.split()).strip()
        if not value:
            raise ValueError("title 不能为空")
        return value


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("消息不能为空")
        return value


def parse_uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{what} 无效") from exc


async def count_messages(db, session_id: uuid.UUID, tenant_id: uuid.UUID) -> int:
    return int((await db.execute(
        select(func.count()).select_from(OmniAgentChatMessageRow).where(
            OmniAgentChatMessageRow.session_id == session_id,
            OmniAgentChatMessageRow.tenant_id == tenant_id,
        )
    )).scalar_one())


@router.get("/sessions")
async def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, _ = owner_scope(user)
    async with async_session_factory() as db:
        base = select(OmniAgentChatSessionRow).where(
            *owner_clause(OmniAgentChatSessionRow, user),
            OmniAgentChatSessionRow.deleted_at.is_(None),
        )
        total = int((await db.execute(
            select(func.count()).select_from(base.subquery())
        )).scalar_one())
        rows = (await db.execute(
            base.order_by(
                OmniAgentChatSessionRow.last_message_at.desc(),
                OmniAgentChatSessionRow.created_at.desc(),
            ).offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
        items = []
        for row in rows:
            await recover_stale_generation(db, row)
            items.append(session_dict(row, await count_messages(db, row.id, tenant_id)))
        await db.commit()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/sessions", status_code=201)
async def create_session(
    req: CreateSessionRequest,
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, owner_id = owner_scope(user)
    sid = uuid.uuid4()
    row = OmniAgentChatSessionRow(
        id=sid,
        tenant_id=tenant_id,
        created_by=owner_id,
        thread_id=f"ae-chat-{sid}",
        title=req.title or "新对话",
        title_source="manual" if req.title else "auto",
    )
    async with async_session_factory() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return session_dict(row)


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, _ = owner_scope(user)
    async with async_session_factory() as db:
        row = await get_owned_session(db, parse_uuid(session_id, "会话 ID"), user)
        await recover_stale_generation(db, row)
        result = session_dict(row, await count_messages(db, row.id, tenant_id))
        await db.commit()
        return result


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str,
    req: RenameSessionRequest,
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, _ = owner_scope(user)
    async with async_session_factory() as db:
        row = await get_owned_session(db, parse_uuid(session_id, "会话 ID"), user, lock=True)
        row.title = req.title
        row.title_source = "manual"
        row.updated_at = utcnow()
        await db.commit()
        await db.refresh(row)
        return session_dict(row, await count_messages(db, row.id, tenant_id))


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user: UserRow | None = Depends(require_internal()),
):
    async with async_session_factory() as db:
        row = await get_owned_session(db, parse_uuid(session_id, "会话 ID"), user, lock=True)
        if row.active_message_id:
            raise HTTPException(status_code=409, detail="生成中不能删除会话")
        row.deleted_at = utcnow()
        row.updated_at = utcnow()
        await db.commit()


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    before_sequence: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=200),
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, _ = owner_scope(user)
    sid = parse_uuid(session_id, "会话 ID")
    async with async_session_factory() as db:
        row = await get_owned_session(db, sid, user)
        await recover_stale_generation(db, row)
        stmt = select(OmniAgentChatMessageRow).where(
            OmniAgentChatMessageRow.session_id == sid,
            OmniAgentChatMessageRow.tenant_id == tenant_id,
        )
        if before_sequence is not None:
            stmt = stmt.where(OmniAgentChatMessageRow.sequence < before_sequence)
        desc_rows = (await db.execute(
            stmt.order_by(OmniAgentChatMessageRow.sequence.desc()).limit(limit + 1)
        )).scalars().all()
        has_more = len(desc_rows) > limit
        rows = list(reversed(desc_rows[:limit]))
        await db.commit()
    return {
        "items": [message_dict(item) for item in rows],
        "next_before_sequence": rows[0].sequence if has_more and rows else None,
    }


@router.post("/sessions/{session_id}/messages/stream")
async def send_message(
    session_id: str,
    req: SendMessageRequest,
    user: UserRow | None = Depends(require_internal()),
):
    row, user_msg, assistant, question = await begin_generation(
        parse_uuid(session_id, "会话 ID"), user, text=req.content
    )
    return StreamingResponse(
        stream_upstream(row, user_msg, assistant, question, user),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/messages/{message_id}/retry")
async def retry_message(
    session_id: str,
    message_id: str,
    user: UserRow | None = Depends(require_internal()),
):
    row, _, assistant, question = await begin_generation(
        parse_uuid(session_id, "会话 ID"),
        user,
        retry_of=parse_uuid(message_id, "消息 ID"),
    )
    return StreamingResponse(
        stream_upstream(row, None, assistant, question, user),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
