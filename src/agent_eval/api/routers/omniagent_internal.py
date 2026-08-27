from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent_eval.config import settings
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    OmniAgentActionRow,
    OmniAgentArtifactRow,
    OmniAgentJobRow,
    OmniAgentScheduleRow,
)
from agent_eval.db_models.tenant_context import (
    TenantContext,
    reset_tenant_context,
    set_tenant_context,
)
from agent_eval.omniagent_runtime.actions import action_dict, prepare_action
from agent_eval.omniagent_runtime.analysis import submit_analysis
from agent_eval.omniagent_runtime.artifacts import (
    artifact_dict,
    get_owned_artifact,
    store_from_settings,
)
from agent_eval.omniagent_runtime.jobs import cancel_owned_job, job_dict
from agent_eval.omniagent_runtime.memories import memory_dict, search_memories
from agent_eval.omniagent_runtime.quota import QuotaExceeded
from agent_eval.omniagent_runtime.runner import runner_configuration_error
from agent_eval.omniagent_runtime.security import (
    ExecutionPrincipal,
    ExecutionTokenError,
    decode_execution_token,
)
from agent_eval.omniagent_runtime.schedules import schedule_dict

router = APIRouter(prefix="/internal/omniagent/v1", tags=["omniagent-internal"])
bearer = HTTPBearer(auto_error=False)


class PrepareActionRequest(BaseModel):
    capability: str = Field(min_length=1, max_length=96)
    arguments: dict
    idempotency_key: str = Field(min_length=1, max_length=128)


class SubmitAnalysisRequest(BaseModel):
    code: str = Field(min_length=1, max_length=1_048_576)
    artifact_ids: list[str] = Field(default_factory=list, max_length=20)


async def execution_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AsyncGenerator[ExecutionPrincipal, None]:
    if not settings.omniagent.execution_enabled:
        raise HTTPException(status_code=503, detail="OmniAgent execution is disabled")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="execution token required")
    try:
        principal = decode_execution_token(credentials.credentials)
    except ExecutionTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    token = set_tenant_context(TenantContext(principal.tenant_id, False))
    try:
        yield principal
    finally:
        reset_tenant_context(token)


def require_scope(scope: str):
    async def dependency(
        principal: ExecutionPrincipal = Depends(execution_principal),
    ) -> ExecutionPrincipal:
        try:
            principal.require_scope(scope)
        except ExecutionTokenError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return principal
    return dependency


def _uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid {label}") from exc


@router.post("/actions/prepare", status_code=201)
async def prepare(
    body: PrepareActionRequest,
    principal: ExecutionPrincipal = Depends(require_scope("action:prepare")),
):
    try:
        async with async_session_factory() as db:
            row = await prepare_action(
                db,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                capability=body.capability,
                arguments=body.arguments,
                idempotency_key=body.idempotency_key,
                session_id=principal.session_id,
                message_id=principal.message_id,
            )
            await db.commit()
            return action_dict(row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/actions/{action_id}")
async def get_action(
    action_id: str,
    principal: ExecutionPrincipal = Depends(require_scope("action:read")),
):
    async with async_session_factory() as db:
        row = (await db.execute(select(OmniAgentActionRow).where(
            OmniAgentActionRow.id == _uuid(action_id, "action_id"),
            OmniAgentActionRow.tenant_id == principal.tenant_id,
            OmniAgentActionRow.requested_by == principal.user_id,
        ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="action not found")
    return action_dict(row)


@router.get("/jobs")
async def jobs(
    limit: int = Query(50, ge=1, le=200),
    principal: ExecutionPrincipal = Depends(require_scope("job:read")),
):
    async with async_session_factory() as db:
        rows = list((await db.execute(select(OmniAgentJobRow).where(
            OmniAgentJobRow.tenant_id == principal.tenant_id,
            OmniAgentJobRow.requested_by == principal.user_id,
        ).order_by(OmniAgentJobRow.created_at.desc()).limit(limit))).scalars())
    return {"items": [job_dict(row) for row in rows]}


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    principal: ExecutionPrincipal = Depends(require_scope("job:read")),
):
    async with async_session_factory() as db:
        row = (await db.execute(select(OmniAgentJobRow).where(
            OmniAgentJobRow.id == _uuid(job_id, "job_id"),
            OmniAgentJobRow.tenant_id == principal.tenant_id,
            OmniAgentJobRow.requested_by == principal.user_id,
        ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job_dict(row)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    principal: ExecutionPrincipal = Depends(require_scope("job:cancel")),
):
    async with async_session_factory() as db:
        row = await cancel_owned_job(
            db,
            job_id=_uuid(job_id, "job_id"),
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        await db.commit()
        return job_dict(row)


@router.get("/memories/search")
async def memories(
    q: str = Query("", max_length=500),
    limit: int = Query(20, ge=1, le=50),
    principal: ExecutionPrincipal = Depends(require_scope("memory:search")),
):
    async with async_session_factory() as db:
        rows = await search_memories(
            db,
            tenant_id=principal.tenant_id,
            owner_id=principal.user_id,
            query=q,
            limit=limit,
        )
    return {"items": [memory_dict(row) for row in rows]}


@router.get("/artifacts")
async def artifacts(
    limit: int = Query(50, ge=1, le=200),
    principal: ExecutionPrincipal = Depends(require_scope("artifact:search")),
):
    async with async_session_factory() as db:
        rows = list(
            (
                await db.execute(
                    select(OmniAgentArtifactRow)
                    .where(
                        OmniAgentArtifactRow.tenant_id == principal.tenant_id,
                        OmniAgentArtifactRow.owner_id == principal.user_id,
                        OmniAgentArtifactRow.state == "available",
                    )
                    .order_by(OmniAgentArtifactRow.created_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )
    return {"items": [artifact_dict(row) for row in rows]}


@router.get("/artifacts/{artifact_id}/content")
async def materialize_artifact(
    artifact_id: str,
    principal: ExecutionPrincipal = Depends(require_scope("artifact:materialize")),
):
    async with async_session_factory() as db:
        row = await get_owned_artifact(
            db,
            artifact_id=_uuid(artifact_id, "artifact_id"),
            tenant_id=principal.tenant_id,
            owner_id=principal.user_id,
            require_available=True,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    store = store_from_settings()
    path = store.path_for_read(row.object_key)
    return FileResponse(
        path,
        media_type=row.mime_type,
        filename=row.filename,
        background=BackgroundTask(store.cleanup_read, path),
    )


@router.post("/analysis/submit", status_code=202)
async def submit_python_analysis(
    body: SubmitAnalysisRequest,
    principal: ExecutionPrincipal = Depends(require_scope("analysis:submit")),
):
    if not settings.omniagent.product_plane_enabled:
        raise HTTPException(status_code=503, detail="OmniAgent product plane is disabled")
    if not settings.omniagent.worker_enabled:
        raise HTTPException(status_code=503, detail="analysis worker is disabled")
    runner_error = runner_configuration_error()
    if runner_error is not None:
        raise HTTPException(status_code=503, detail=runner_error)
    if settings.omniagent.artifact_scanner not in {"development", "clamav"}:
        raise HTTPException(status_code=503, detail="artifact scanner is unavailable")
    try:
        ids = [_uuid(value, "artifact_id") for value in body.artifact_ids]
        async with async_session_factory() as db:
            job = await submit_analysis(
                db,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=principal.session_id,
                message_id=principal.message_id,
                code=body.code,
                artifact_ids=ids,
            )
            await db.commit()
            return job_dict(job)
    except QuotaExceeded as exc:
        raise HTTPException(status_code=429, detail="QUOTA_EXCEEDED") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/schedules")
async def schedules(
    principal: ExecutionPrincipal = Depends(require_scope("schedule:read")),
):
    async with async_session_factory() as db:
        rows = list(
            (
                await db.execute(
                    select(OmniAgentScheduleRow)
                    .where(
                        OmniAgentScheduleRow.tenant_id == principal.tenant_id,
                        OmniAgentScheduleRow.owner_id == principal.user_id,
                    )
                    .order_by(OmniAgentScheduleRow.created_at.desc())
                )
            ).scalars()
        )
    return {"items": [schedule_dict(row) for row in rows]}
