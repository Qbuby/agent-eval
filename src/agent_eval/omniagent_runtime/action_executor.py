from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db_models.tables import (
    DatasetMetadataRow,
    OmniAgentActionRow,
    OmniAgentJobRow,
)
from agent_eval.omniagent_runtime.artifacts import get_owned_artifact, pin_artifact
from agent_eval.omniagent_runtime.events import append_event
from agent_eval.omniagent_runtime.jobs import finish_job
from agent_eval.omniagent_runtime.memories import delete_memory, save_memory
from agent_eval.omniagent_runtime.schedules import apply_schedule_action
from agent_eval.omniagent_runtime.state import ACTION_TRANSITIONS, require_transition


class ActionExecutionError(ValueError):
    pass


async def reconcile_terminal_action_jobs(db: AsyncSession) -> int:
    """Move actions whose execution job is terminal out of non-terminal states."""
    rows = list(
        (
            await db.execute(
                select(OmniAgentActionRow, OmniAgentJobRow)
                .join(OmniAgentJobRow, OmniAgentJobRow.id == OmniAgentActionRow.job_id)
                .where(
                    OmniAgentActionRow.state.in_(["approved", "executing"]),
                    OmniAgentJobRow.status.in_(["failed", "cancelled", "expired"]),
                )
                .with_for_update(of=OmniAgentActionRow, skip_locked=True)
            )
        ).all()
    )
    for action, job in rows:
        target = "expired" if job.status == "expired" and action.state == "approved" else "failed"
        if target not in ACTION_TRANSITIONS[action.state]:
            target = "cancelled"
        require_transition(ACTION_TRANSITIONS, action.state, target)
        action.state = target
        action.finished_at = datetime.now(timezone.utc)
        action.terminal_summary = {
            "code": job.error_code or f"JOB_{job.status.upper()}",
            "job_id": str(job.id),
        }
        await append_event(
            db,
            tenant_id=action.tenant_id,
            user_id=action.requested_by,
            session_id=action.session_id,
            message_id=action.message_id,
            event_type=f"action.{target}",
            entity_type="action",
            entity_id=str(action.id),
            payload={
                "state": target,
                "capability": action.capability,
                "code": action.terminal_summary["code"],
            },
        )
    return len(rows)


async def execute_action_job(
    db: AsyncSession,
    *,
    job: OmniAgentJobRow,
    worker_id: str,
) -> bool:
    """Execute one approved fixed action and finish its leased job atomically."""
    if job.kind != "action.execute" or job.action_id is None:
        raise ActionExecutionError("job is not an action execution")

    action = (
        await db.execute(
            select(OmniAgentActionRow)
            .where(
                OmniAgentActionRow.id == job.action_id,
                OmniAgentActionRow.tenant_id == job.tenant_id,
                OmniAgentActionRow.requested_by == job.requested_by,
                OmniAgentActionRow.job_id == job.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if action is None:
        raise ActionExecutionError("approved action is unavailable")
    if action.state != "approved":
        raise ActionExecutionError(f"action is not executable from state {action.state}")

    now = datetime.now(timezone.utc)
    if action.expires_at <= now:
        require_transition(ACTION_TRANSITIONS, action.state, "expired")
        action.state = "expired"
        action.finished_at = now
        action.terminal_summary = {"code": "ACTION_EXPIRED"}
        await append_event(
            db,
            tenant_id=action.tenant_id,
            user_id=action.requested_by,
            session_id=action.session_id,
            message_id=action.message_id,
            event_type="action.expired",
            entity_type="action",
            entity_id=str(action.id),
            payload={"state": action.state, "capability": action.capability},
        )
        return await finish_job(
            db,
            job=job,
            worker_id=worker_id,
            status="expired",
            error_code="ACTION_EXPIRED",
            error_message="approved action expired before execution",
        )

    require_transition(ACTION_TRANSITIONS, action.state, "executing")
    action.state = "executing"
    action.execution_started_at = now
    await append_event(
        db,
        tenant_id=action.tenant_id,
        user_id=action.requested_by,
        session_id=action.session_id,
        message_id=action.message_id,
        event_type="action.executing",
        entity_type="action",
        entity_id=str(action.id),
        payload={"state": action.state, "capability": action.capability},
    )

    try:
        summary = await _execute_fixed_action(db, action)
    except Exception as exc:
        require_transition(ACTION_TRANSITIONS, action.state, "failed")
        action.state = "failed"
        action.finished_at = datetime.now(timezone.utc)
        action.terminal_summary = {
            "code": type(exc).__name__.upper(),
            "message": str(exc)[:1000],
        }
        await append_event(
            db,
            tenant_id=action.tenant_id,
            user_id=action.requested_by,
            session_id=action.session_id,
            message_id=action.message_id,
            event_type="action.failed",
            entity_type="action",
            entity_id=str(action.id),
            payload={
                "state": action.state,
                "capability": action.capability,
                "code": type(exc).__name__.upper(),
            },
        )
        return await finish_job(
            db,
            job=job,
            worker_id=worker_id,
            status="failed",
            error_code="ACTION_FAILED",
            error_message=str(exc)[:4000],
        )

    require_transition(ACTION_TRANSITIONS, action.state, "succeeded")
    action.state = "succeeded"
    action.finished_at = datetime.now(timezone.utc)
    action.terminal_summary = summary
    await append_event(
        db,
        tenant_id=action.tenant_id,
        user_id=action.requested_by,
        session_id=action.session_id,
        message_id=action.message_id,
        event_type="action.succeeded",
        entity_type="action",
        entity_id=str(action.id),
        payload={"state": action.state, "capability": action.capability, "summary": summary},
    )
    return await finish_job(
        db,
        job=job,
        worker_id=worker_id,
        status="succeeded",
        result={"action_id": str(action.id), "summary": summary},
    )


async def _execute_fixed_action(
    db: AsyncSession,
    action: OmniAgentActionRow,
) -> dict[str, Any]:
    arguments = action.arguments
    owner_id = action.requested_by
    if owner_id is None:
        raise ActionExecutionError("fixed actions require an authenticated owner")

    if action.capability == "memory.save":
        memory = await save_memory(
            db,
            tenant_id=action.tenant_id,
            owner_id=owner_id,
            title=arguments["title"],
            content=arguments["content"],
            tags=arguments.get("tags"),
            source_action_id=action.id,
        )
        return {"memory_id": str(memory.id), "saved": True}

    if action.capability == "memory.delete":
        deleted = await delete_memory(
            db,
            memory_id=_uuid_argument(arguments, "memory_id"),
            tenant_id=action.tenant_id,
            owner_id=owner_id,
        )
        if not deleted:
            raise ActionExecutionError("memory not found")
        return {"memory_id": arguments["memory_id"], "deleted": True}

    if action.capability == "artifact.pin":
        artifact = await get_owned_artifact(
            db,
            artifact_id=_uuid_argument(arguments, "artifact_id"),
            tenant_id=action.tenant_id,
            owner_id=owner_id,
            require_available=True,
        )
        if artifact is None:
            raise ActionExecutionError("artifact not found")
        await pin_artifact(artifact)
        return {"artifact_id": str(artifact.id), "retention": artifact.retention}

    if action.capability in {"dataset.archive", "dataset.activate"}:
        name = str(arguments["name"]).strip()
        if not name:
            raise ActionExecutionError("dataset name is required")
        dataset = (
            await db.execute(
                select(DatasetMetadataRow)
                .where(
                    DatasetMetadataRow.tenant_id == action.tenant_id,
                    DatasetMetadataRow.dataset_name == name,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if dataset is None:
            raise ActionExecutionError("dataset not found")
        dataset.status = "archived" if action.capability.endswith("archive") else "active"
        dataset.updated_at = datetime.now(timezone.utc)
        return {"dataset_name": name, "status": dataset.status}

    if action.capability in {"schedule.create", "schedule.update", "schedule.resume"}:
        return await apply_schedule_action(db, action)

    raise ActionExecutionError("capability has no registered executor")


def _uuid_argument(arguments: dict[str, Any], key: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(arguments[key]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ActionExecutionError(f"invalid {key}") from exc
