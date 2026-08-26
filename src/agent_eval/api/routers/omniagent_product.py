from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent_eval.auth.dependencies import require_internal
from agent_eval.config import settings
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    OmniAgentActionRow,
    OmniAgentArtifactRow,
    OmniAgentJobRow,
    OmniAgentNotificationRow,
    OmniAgentScheduleRow,
    UserRow,
)
from agent_eval.omniagent_runtime.actions import action_dict, decide_action
from agent_eval.omniagent_runtime.artifacts import (
    ArtifactError,
    artifact_dict,
    get_owned_artifact,
    ingest_artifact,
    store_from_settings,
)
from agent_eval.omniagent_runtime.events import event_dict, list_events
from agent_eval.omniagent_runtime.jobs import cancel_owned_job, job_dict
from agent_eval.omniagent_runtime.memories import delete_memory, memory_dict, search_memories
from agent_eval.omniagent_runtime.notifications import notification_dict
from agent_eval.omniagent_runtime.schedules import schedule_dict
from agent_eval.services.omniagent_chat import get_owned_session, owner_scope, utcnow

router = APIRouter(prefix="/api/omniagent", tags=["omniagent-product"])


class ActionDecisionRequest(BaseModel):
    decision: str
    digest: str = Field(min_length=64, max_length=64)


def _uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid {label}") from exc


def _owner(column, owner_id: uuid.UUID | None):
    return column.is_(None) if owner_id is None else column == owner_id


def _require_mutations_enabled() -> None:
    if not settings.omniagent.product_plane_enabled:
        raise HTTPException(status_code=503, detail="OmniAgent product plane is disabled")


@router.get("/events")
async def events(
    request: Request,
    after: int = Query(0, ge=0),
    session_id: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    stream: bool = False,
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, owner_id = owner_scope(user)
    sid = _uuid(session_id, "session_id") if session_id else None

    async def load(cursor: int):
        async with async_session_factory() as db:
            return await list_events(
                db,
                tenant_id=tenant_id,
                user_id=owner_id,
                after=cursor,
                session_id=sid,
                limit=limit,
            )

    if not stream:
        rows = await load(after)
        return {"items": [event_dict(row) for row in rows], "cursor": rows[-1].id if rows else after}

    async def generate() -> AsyncGenerator[str, None]:
        cursor = after
        while not await request.is_disconnected():
            rows = await load(cursor)
            if rows:
                for row in rows:
                    cursor = row.id
                    payload = json.dumps(event_dict(row), ensure_ascii=False, default=str)
                    yield f"id: {cursor}\nevent: {row.event_type}\ndata: {payload}\n\n"
                continue
            yield ": heartbeat\n\n"
            await asyncio.sleep(10)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/jobs")
async def jobs(
    limit: int = Query(50, ge=1, le=200),
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, owner_id = owner_scope(user)
    async with async_session_factory() as db:
        rows = list((await db.execute(
            select(OmniAgentJobRow).where(
                OmniAgentJobRow.tenant_id == tenant_id,
                _owner(OmniAgentJobRow.requested_by, owner_id),
            ).order_by(OmniAgentJobRow.created_at.desc()).limit(limit)
        )).scalars())
    return {"items": [job_dict(row) for row in rows]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: UserRow | None = Depends(require_internal())):
    tenant_id, owner_id = owner_scope(user)
    async with async_session_factory() as db:
        row = (await db.execute(select(OmniAgentJobRow).where(
            OmniAgentJobRow.id == _uuid(job_id, "job_id"),
            OmniAgentJobRow.tenant_id == tenant_id,
            _owner(OmniAgentJobRow.requested_by, owner_id),
        ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job_dict(row)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, user: UserRow | None = Depends(require_internal())):
    _require_mutations_enabled()
    tenant_id, owner_id = owner_scope(user)
    async with async_session_factory() as db:
        row = await cancel_owned_job(
            db, job_id=_uuid(job_id, "job_id"), tenant_id=tenant_id, user_id=owner_id
        )
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        await db.commit()
        return job_dict(row)


@router.get("/actions")
async def actions(
    state: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, owner_id = owner_scope(user)
    stmt = select(OmniAgentActionRow).where(
        OmniAgentActionRow.tenant_id == tenant_id,
        _owner(OmniAgentActionRow.requested_by, owner_id),
    )
    if state:
        stmt = stmt.where(OmniAgentActionRow.state == state)
    async with async_session_factory() as db:
        rows = list((await db.execute(
            stmt.order_by(OmniAgentActionRow.created_at.desc()).limit(limit)
        )).scalars())
    return {"items": [action_dict(row) for row in rows]}


@router.get("/actions/{action_id}")
async def get_action(action_id: str, user: UserRow | None = Depends(require_internal())):
    tenant_id, owner_id = owner_scope(user)
    async with async_session_factory() as db:
        row = (await db.execute(select(OmniAgentActionRow).where(
            OmniAgentActionRow.id == _uuid(action_id, "action_id"),
            OmniAgentActionRow.tenant_id == tenant_id,
            _owner(OmniAgentActionRow.requested_by, owner_id),
        ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="action not found")
    return action_dict(row)


@router.post("/actions/{action_id}/decision")
async def action_decision(
    action_id: str,
    body: ActionDecisionRequest,
    user: UserRow | None = Depends(require_internal()),
):
    _require_mutations_enabled()
    tenant_id, owner_id = owner_scope(user)
    try:
        async with async_session_factory() as db:
            row = await decide_action(
                db,
                action_id=_uuid(action_id, "action_id"),
                tenant_id=tenant_id,
                user_id=owner_id,
                digest=body.digest,
                decision=body.decision,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="action not found")
            await db.commit()
            return action_dict(row)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/memories")
async def memories(
    q: str = Query("", max_length=500),
    limit: int = Query(20, ge=1, le=50),
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, owner_id = owner_scope(user)
    async with async_session_factory() as db:
        rows = await search_memories(
            db, tenant_id=tenant_id, owner_id=owner_id, query=q, limit=limit
        )
    return {"items": [memory_dict(row) for row in rows]}


@router.delete("/memories/{memory_id}", status_code=204)
async def remove_memory(memory_id: str, user: UserRow | None = Depends(require_internal())):
    _require_mutations_enabled()
    tenant_id, owner_id = owner_scope(user)
    async with async_session_factory() as db:
        deleted = await delete_memory(
            db,
            memory_id=_uuid(memory_id, "memory_id"),
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="memory not found")
        await db.commit()


@router.get("/artifacts")
async def list_artifacts(
    limit: int = Query(50, ge=1, le=200),
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, owner_id = owner_scope(user)
    async with async_session_factory() as db:
        rows = list(
            (
                await db.execute(
                    select(OmniAgentArtifactRow)
                    .where(
                        OmniAgentArtifactRow.tenant_id == tenant_id,
                        _owner(OmniAgentArtifactRow.owner_id, owner_id),
                        OmniAgentArtifactRow.state.notin_(["deleted", "expired"]),
                    )
                    .order_by(OmniAgentArtifactRow.created_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )
    return {"items": [artifact_dict(row) for row in rows]}


@router.post("/artifacts", status_code=201)
async def upload_artifact(
    file: UploadFile = File(...),
    session_id: str | None = Query(None),
    user: UserRow | None = Depends(require_internal()),
):
    _require_mutations_enabled()
    tenant_id, owner_id = owner_scope(user)
    sid = _uuid(session_id, "session_id") if session_id else None
    storage = store_from_settings()
    row: OmniAgentArtifactRow | None = None
    async with async_session_factory() as db:
        try:
            if sid is not None:
                await get_owned_session(db, sid, user)
            row = await ingest_artifact(
                db,
                tenant_id=tenant_id,
                owner_id=owner_id,
                filename=file.filename or "",
                declared_mime=file.content_type,
                source=file,
                session_id=sid,
                store=storage,
            )
            await db.commit()
            return artifact_dict(row)
        except ArtifactError as exc:
            await db.rollback()
            if row is not None:
                storage.delete(row.object_key)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            await db.rollback()
            if row is not None:
                storage.delete(row.object_key)
            raise
        finally:
            await file.close()


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, owner_id = owner_scope(user)
    async with async_session_factory() as db:
        row = await get_owned_artifact(
            db,
            artifact_id=_uuid(artifact_id, "artifact_id"),
            tenant_id=tenant_id,
            owner_id=owner_id,
            require_available=True,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    try:
        store = store_from_settings()
        path = store.path_for_read(row.object_key)
    except ArtifactError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    return FileResponse(
        path,
        media_type=row.mime_type,
        filename=row.filename,
        background=BackgroundTask(store.cleanup_read, path),
    )


@router.get("/notifications")
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, owner_id = owner_scope(user)
    stmt = select(OmniAgentNotificationRow).where(
        OmniAgentNotificationRow.tenant_id == tenant_id,
        _owner(OmniAgentNotificationRow.user_id, owner_id),
    )
    if unread_only:
        stmt = stmt.where(OmniAgentNotificationRow.read_at.is_(None))
    async with async_session_factory() as db:
        rows = list(
            (
                await db.execute(
                    stmt.order_by(OmniAgentNotificationRow.created_at.desc()).limit(limit)
                )
            ).scalars()
        )
    return {"items": [notification_dict(row) for row in rows]}


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, owner_id = owner_scope(user)
    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(OmniAgentNotificationRow)
                .where(
                    OmniAgentNotificationRow.id == _uuid(notification_id, "notification_id"),
                    OmniAgentNotificationRow.tenant_id == tenant_id,
                    _owner(OmniAgentNotificationRow.user_id, owner_id),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="notification not found")
        row.read_at = row.read_at or utcnow()
        await db.commit()
        return notification_dict(row)


@router.get("/schedules")
async def list_schedules(
    user: UserRow | None = Depends(require_internal()),
):
    tenant_id, owner_id = owner_scope(user)
    async with async_session_factory() as db:
        rows = list(
            (
                await db.execute(
                    select(OmniAgentScheduleRow)
                    .where(
                        OmniAgentScheduleRow.tenant_id == tenant_id,
                        _owner(OmniAgentScheduleRow.owner_id, owner_id),
                    )
                    .order_by(OmniAgentScheduleRow.created_at.desc())
                )
            ).scalars()
        )
    return {"items": [schedule_dict(row) for row in rows]}


@router.post("/schedules/{schedule_id}/pause")
async def pause_schedule(
    schedule_id: str,
    user: UserRow | None = Depends(require_internal()),
):
    _require_mutations_enabled()
    tenant_id, owner_id = owner_scope(user)
    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(OmniAgentScheduleRow)
                .where(
                    OmniAgentScheduleRow.id == _uuid(schedule_id, "schedule_id"),
                    OmniAgentScheduleRow.tenant_id == tenant_id,
                    _owner(OmniAgentScheduleRow.owner_id, owner_id),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="schedule not found")
        row.enabled = False
        row.next_run_at = None
        row.updated_at = utcnow()
        await db.commit()
        return schedule_dict(row)
