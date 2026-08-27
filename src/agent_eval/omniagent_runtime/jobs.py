from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db_models.tables import OmniAgentJobAttemptRow, OmniAgentJobRow
from agent_eval.omniagent_runtime.events import append_event
from agent_eval.omniagent_runtime.notifications import create_notification
from agent_eval.omniagent_runtime.state import JOB_TRANSITIONS, require_transition

TERMINAL_JOB_STATES = frozenset({"succeeded", "failed", "cancelled", "expired"})
ACTIVE_JOB_STATES = frozenset({"provisioning", "running"})


def job_dict(row: OmniAgentJobRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "status": row.status,
        "result": row.result,
        "error": (
            {"code": row.error_code, "message": row.error_message}
            if row.error_code or row.error_message
            else None
        ),
        "attempt_count": row.attempt_count,
        "max_attempts": row.max_attempts,
        "session_id": str(row.session_id) if row.session_id else None,
        "action_id": str(row.action_id) if row.action_id else None,
        "usage": row.usage,
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
    }


def infrastructure_recovery_state(
    *,
    attempt_count: int,
    max_attempts: int,
    expires_at: datetime | None,
    now: datetime,
) -> str:
    """Choose the only legal outcome after an infrastructure-owned failure."""
    if expires_at is not None and expires_at <= now:
        return "expired"
    if attempt_count < max_attempts:
        return "queued"
    return "failed"


async def create_job(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    kind: str,
    spec: dict[str, Any],
    session_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
    action_id: uuid.UUID | None = None,
    max_attempts: int = 3,
    expires_at: datetime | None = None,
) -> OmniAgentJobRow:
    row = OmniAgentJobRow(
        tenant_id=tenant_id,
        kind=kind,
        status="queued",
        spec=spec,
        requested_by=user_id,
        session_id=session_id,
        message_id=message_id,
        action_id=action_id,
        max_attempts=max(1, min(max_attempts, 3)),
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    await append_event(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        message_id=message_id,
        event_type="job.queued",
        entity_type="job",
        entity_id=str(row.id),
        payload={"kind": kind, "status": "queued"},
    )
    return row


async def claim_next_job(
    db: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = 60,
    kinds: frozenset[str] | None = None,
    tenant_ids: frozenset[uuid.UUID] | None = None,
) -> tuple[OmniAgentJobRow, OmniAgentJobAttemptRow] | None:
    if tenant_ids is not None and not tenant_ids:
        return None
    now = datetime.now(timezone.utc)
    conditions = [
        OmniAgentJobRow.status == "queued",
        OmniAgentJobRow.available_at <= now,
        (OmniAgentJobRow.expires_at.is_(None) | (OmniAgentJobRow.expires_at > now)),
    ]
    if kinds:
        conditions.append(OmniAgentJobRow.kind.in_(kinds))
    if tenant_ids is not None:
        conditions.append(OmniAgentJobRow.tenant_id.in_(tenant_ids))
    stmt = (
        select(OmniAgentJobRow)
        .where(*conditions)
        .order_by(OmniAgentJobRow.priority, OmniAgentJobRow.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None
    require_transition(JOB_TRANSITIONS, job.status, "provisioning")
    job.status = "provisioning"
    job.attempt_count += 1
    job.lease_owner = worker_id
    job.lease_expires_at = now + timedelta(seconds=max(15, lease_seconds))
    job.started_at = job.started_at or now
    attempt = OmniAgentJobAttemptRow(
        tenant_id=job.tenant_id,
        job_id=job.id,
        attempt_no=job.attempt_count,
        worker_id=worker_id,
        status="running",
        started_at=now,
        heartbeat_at=now,
    )
    db.add(attempt)
    await append_event(
        db,
        tenant_id=job.tenant_id,
        user_id=job.requested_by,
        session_id=job.session_id,
        message_id=job.message_id,
        event_type="job.provisioning",
        entity_type="job",
        entity_id=str(job.id),
        payload={"kind": job.kind, "status": job.status, "attempt": job.attempt_count},
    )
    await db.flush()
    return job, attempt


async def mark_job_running(
    db: AsyncSession, *, job_id: uuid.UUID, worker_id: str, runtime_ref: str | None = None
) -> bool:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(OmniAgentJobRow)
        .where(
            OmniAgentJobRow.id == job_id,
            OmniAgentJobRow.status == "provisioning",
            OmniAgentJobRow.lease_owner == worker_id,
            OmniAgentJobRow.lease_expires_at > now,
        )
        .values(status="running", updated_at=now)
        .returning(
            OmniAgentJobRow.tenant_id,
            OmniAgentJobRow.requested_by,
            OmniAgentJobRow.session_id,
            OmniAgentJobRow.message_id,
            OmniAgentJobRow.kind,
            OmniAgentJobRow.attempt_count,
        )
    )
    changed = result.first()
    if changed is None:
        return False
    await db.execute(
        update(OmniAgentJobAttemptRow)
        .where(
            OmniAgentJobAttemptRow.job_id == job_id,
            OmniAgentJobAttemptRow.attempt_no == changed.attempt_count,
            OmniAgentJobAttemptRow.worker_id == worker_id,
            OmniAgentJobAttemptRow.status == "running",
        )
        .values(runtime_ref=runtime_ref, heartbeat_at=now)
    )
    await append_event(
        db,
        tenant_id=changed.tenant_id,
        user_id=changed.requested_by,
        session_id=changed.session_id,
        message_id=changed.message_id,
        event_type="job.running",
        entity_type="job",
        entity_id=str(job_id),
        payload={"kind": changed.kind, "status": "running", "attempt": changed.attempt_count},
    )
    return True


async def heartbeat_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    lease_seconds: int = 60,
) -> bool:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(OmniAgentJobRow)
        .where(
            OmniAgentJobRow.id == job_id,
            OmniAgentJobRow.status.in_(ACTIVE_JOB_STATES),
            OmniAgentJobRow.lease_owner == worker_id,
            OmniAgentJobRow.lease_expires_at > now,
        )
        .values(
            lease_expires_at=now + timedelta(seconds=max(15, lease_seconds)),
            updated_at=now,
        )
        .returning(OmniAgentJobRow.attempt_count)
    )
    changed = result.first()
    if changed is None:
        return False
    await db.execute(
        update(OmniAgentJobAttemptRow)
        .where(
            OmniAgentJobAttemptRow.job_id == job_id,
            OmniAgentJobAttemptRow.attempt_no == changed.attempt_count,
            OmniAgentJobAttemptRow.worker_id == worker_id,
            OmniAgentJobAttemptRow.status == "running",
        )
        .values(heartbeat_at=now)
    )
    return True


async def finish_job(
    db: AsyncSession,
    *,
    job: OmniAgentJobRow,
    worker_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    usage: dict[str, Any] | None = None,
    infrastructure_failure: bool = False,
) -> bool:
    if infrastructure_failure and status != "failed":
        raise ValueError("infrastructure failures must be reported with status=failed")
    now = datetime.now(timezone.utc)
    locked = (
        await db.execute(
            select(OmniAgentJobRow)
            .where(OmniAgentJobRow.id == job.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        locked is None
        or locked.status not in ACTIVE_JOB_STATES
        or locked.lease_owner != worker_id
        or locked.lease_expires_at is None
        or locked.lease_expires_at <= now
    ):
        return False

    target = status
    if infrastructure_failure:
        target = infrastructure_recovery_state(
            attempt_count=locked.attempt_count,
            max_attempts=locked.max_attempts,
            expires_at=locked.expires_at,
            now=now,
        )
    require_transition(JOB_TRANSITIONS, locked.status, target)

    attempt = (
        await db.execute(
            select(OmniAgentJobAttemptRow).where(
                OmniAgentJobAttemptRow.job_id == locked.id,
                OmniAgentJobAttemptRow.attempt_no == locked.attempt_count,
                OmniAgentJobAttemptRow.worker_id == worker_id,
            )
        )
    ).scalar_one_or_none()
    if attempt is not None:
        attempt.status = "failed" if infrastructure_failure else status
        attempt.error_code = error_code
        attempt.error_message = error_message
        attempt.infrastructure_failure = infrastructure_failure
        attempt.finished_at = now
        attempt.heartbeat_at = now

    locked.status = target
    locked.result = None if target == "queued" else result
    locked.error_code = error_code
    locked.error_message = error_message
    locked.usage = usage
    locked.lease_owner = None
    locked.lease_expires_at = None
    if target == "queued":
        locked.available_at = now + timedelta(seconds=min(60, 2 ** locked.attempt_count))
        locked.finished_at = None
    else:
        locked.finished_at = now

    event_type = "job.retry_scheduled" if target == "queued" else f"job.{target}"
    event = await append_event(
        db,
        tenant_id=locked.tenant_id,
        user_id=locked.requested_by,
        session_id=locked.session_id,
        message_id=locked.message_id,
        event_type=event_type,
        entity_type="job",
        entity_id=str(locked.id),
        payload={
            "kind": locked.kind,
            "status": target,
            "attempt": locked.attempt_count,
            "error_code": error_code,
        },
    )
    if target in TERMINAL_JOB_STATES:
        await create_notification(
            db,
            tenant_id=locked.tenant_id,
            user_id=locked.requested_by,
            event_id=event.id,
            kind="job_completed",
            title=f"OmniAgent task {target}",
            body=(
                f"{locked.kind} completed with status {target}."
                if not error_code
                else f"{locked.kind} completed with status {target} ({error_code})."
            ),
            link=f"/omniagent?job={locked.id}",
        )
    return True


async def recover_expired_leases(
    db: AsyncSession,
    *,
    limit: int = 100,
) -> int:
    now = datetime.now(timezone.utc)
    jobs = list(
        (
            await db.execute(
                select(OmniAgentJobRow)
                .where(
                    OmniAgentJobRow.status.in_(ACTIVE_JOB_STATES),
                    OmniAgentJobRow.lease_expires_at <= now,
                )
                .order_by(OmniAgentJobRow.lease_expires_at)
                .with_for_update(skip_locked=True)
                .limit(max(1, min(limit, 500)))
            )
        ).scalars()
    )
    for job in jobs:
        target = infrastructure_recovery_state(
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            expires_at=job.expires_at,
            now=now,
        )
        require_transition(JOB_TRANSITIONS, job.status, target)
        attempt = (
            await db.execute(
                select(OmniAgentJobAttemptRow).where(
                    OmniAgentJobAttemptRow.job_id == job.id,
                    OmniAgentJobAttemptRow.attempt_no == job.attempt_count,
                )
            )
        ).scalar_one_or_none()
        if attempt is not None:
            attempt.status = "failed"
            attempt.infrastructure_failure = True
            attempt.error_code = "LEASE_EXPIRED"
            attempt.error_message = "worker heartbeat lease expired"
            attempt.finished_at = now
            attempt.heartbeat_at = now
        job.status = target
        job.error_code = "LEASE_EXPIRED"
        job.error_message = "worker heartbeat lease expired"
        job.lease_owner = None
        job.lease_expires_at = None
        if target == "queued":
            job.available_at = now + timedelta(seconds=min(60, 2 ** job.attempt_count))
            job.finished_at = None
        else:
            job.finished_at = now
        await append_event(
            db,
            tenant_id=job.tenant_id,
            user_id=job.requested_by,
            session_id=job.session_id,
            message_id=job.message_id,
            event_type="job.retry_scheduled" if target == "queued" else f"job.{target}",
            entity_type="job",
            entity_id=str(job.id),
            payload={
                "kind": job.kind,
                "status": target,
                "attempt": job.attempt_count,
                "error_code": "LEASE_EXPIRED",
            },
        )
    return len(jobs)


async def cancel_owned_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> OmniAgentJobRow | None:
    owner = (
        OmniAgentJobRow.requested_by.is_(None)
        if user_id is None
        else OmniAgentJobRow.requested_by == user_id
    )
    job = (
        await db.execute(
            select(OmniAgentJobRow)
            .where(OmniAgentJobRow.id == job_id, OmniAgentJobRow.tenant_id == tenant_id, owner)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        return None
    if job.status in TERMINAL_JOB_STATES:
        return job
    require_transition(JOB_TRANSITIONS, job.status, "cancelled")
    now = datetime.now(timezone.utc)
    prior_owner = job.lease_owner
    job.status = "cancelled"
    job.cancelled_at = now
    job.finished_at = now
    job.lease_owner = None
    job.lease_expires_at = None
    if job.attempt_count and prior_owner:
        await db.execute(
            update(OmniAgentJobAttemptRow)
            .where(
                OmniAgentJobAttemptRow.job_id == job.id,
                OmniAgentJobAttemptRow.attempt_no == job.attempt_count,
                OmniAgentJobAttemptRow.status == "running",
            )
            .values(status="cancelled", finished_at=now, heartbeat_at=now)
        )
    await append_event(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=job.session_id,
        message_id=job.message_id,
        event_type="job.cancelled",
        entity_type="job",
        entity_id=str(job.id),
        payload={"kind": job.kind, "status": job.status},
    )
    return job
