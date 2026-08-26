from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.config import settings
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    OmniAgentActionRow,
    OmniAgentScheduleRow,
)
from agent_eval.omniagent_runtime.events import append_event
from agent_eval.omniagent_runtime.jobs import create_job
from agent_eval.omniagent_runtime.policy import DEFAULT_POLICY
from agent_eval.omniagent_runtime.security import canonical_digest

SCHEDULABLE_CAPABILITIES = frozenset({"dataset.archive", "dataset.activate"})


class ScheduleError(ValueError):
    pass


def _zone(timezone_name: str):
    if timezone_name == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        # Windows and slim containers may not ship an IANA database. Keep the
        # product default deterministic without accepting arbitrary fake zones.
        if timezone_name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name="Asia/Shanghai")
        raise ScheduleError("invalid IANA timezone") from exc


def validate_schedule(value: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScheduleError("schedule must be an object")
    zone = _zone(timezone_name)
    kind = value.get("kind")
    if kind == "once":
        try:
            at = datetime.fromisoformat(str(value["at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ScheduleError("once schedule requires an ISO datetime") from exc
        if at.tzinfo is None:
            at = at.replace(tzinfo=zone)
        at = at.astimezone(timezone.utc)
        if at <= datetime.now(timezone.utc):
            raise ScheduleError("once schedule must be in the future")
        return {"kind": "once", "at": at.isoformat()}
    if kind == "interval":
        try:
            minutes = int(value["minutes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ScheduleError("interval schedule requires minutes") from exc
        if minutes < 15:
            raise ScheduleError("interval must be at least 15 minutes")
        return {"kind": "interval", "minutes": minutes}
    if kind == "daily":
        at = str(value.get("at") or "")
        try:
            hour_text, minute_text = at.split(":")
            hour, minute = int(hour_text), int(minute_text)
        except (ValueError, TypeError) as exc:
            raise ScheduleError("daily schedule requires HH:MM") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ScheduleError("daily schedule requires a valid HH:MM")
        return {"kind": "daily", "at": f"{hour:02d}:{minute:02d}"}
    raise ScheduleError("schedule kind must be once, interval, or daily")


def compute_next_run(
    schedule: dict[str, Any], timezone_name: str, *, now: datetime | None = None
) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    kind = schedule.get("kind")
    if kind == "once":
        at = datetime.fromisoformat(str(schedule["at"]).replace("Z", "+00:00"))
        return at.astimezone(timezone.utc) if at > now else None
    if kind == "interval":
        return now + timedelta(minutes=int(schedule["minutes"]))
    if kind == "daily":
        zone = _zone(timezone_name)
        local_now = now.astimezone(zone)
        hour, minute = (int(part) for part in str(schedule["at"]).split(":"))
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)
    return None


def schedule_dict(row: OmniAgentScheduleRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "capability": row.capability,
        "arguments": row.arguments,
        "schedule": row.schedule,
        "timezone": row.timezone,
        "version": row.version,
        "enabled": row.enabled,
        "next_run_at": row.next_run_at,
        "last_run_at": row.last_run_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def apply_schedule_action(
    db: AsyncSession, action: OmniAgentActionRow
) -> dict[str, Any]:
    owner_id = action.requested_by
    if owner_id is None:
        raise ScheduleError("schedule actions require an authenticated owner")
    args = dict(action.arguments)
    if action.capability == "schedule.create":
        active = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(OmniAgentScheduleRow)
                    .where(
                        OmniAgentScheduleRow.tenant_id == action.tenant_id,
                        OmniAgentScheduleRow.owner_id == owner_id,
                        OmniAgentScheduleRow.enabled.is_(True),
                    )
                )
            ).scalar_one()
        )
        if active >= DEFAULT_POLICY.enabled_schedules_user:
            raise ScheduleError("QUOTA_EXCEEDED: enabled schedules")
        capability, arguments = _validate_target(args)
        timezone_name = str(args.get("timezone") or "Asia/Shanghai")
        schedule = validate_schedule(args["schedule"], timezone_name)
        row = OmniAgentScheduleRow(
            tenant_id=action.tenant_id,
            owner_id=owner_id,
            name=str(args["name"]).strip()[:256],
            capability=capability,
            arguments=arguments,
            argument_digest=canonical_digest({"capability": capability, "arguments": arguments}),
            schedule=schedule,
            timezone=timezone_name,
            version=1,
            enabled=True,
            approved_action_id=action.id,
            next_run_at=compute_next_run(schedule, timezone_name),
        )
        if not row.name:
            raise ScheduleError("schedule name is required")
        db.add(row)
        await db.flush()
        return {"schedule_id": str(row.id), "version": row.version, "enabled": True}

    schedule_id = _uuid_arg(args, "schedule_id")
    row = (
        await db.execute(
            select(OmniAgentScheduleRow)
            .where(
                OmniAgentScheduleRow.id == schedule_id,
                OmniAgentScheduleRow.tenant_id == action.tenant_id,
                OmniAgentScheduleRow.owner_id == owner_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise ScheduleError("schedule not found")
    if action.capability == "schedule.resume":
        schedule = validate_schedule(row.schedule, row.timezone)
        row.enabled = True
        row.next_run_at = compute_next_run(schedule, row.timezone)
    elif action.capability == "schedule.update":
        merged = {
            "name": args.get("name", row.name),
            "capability": args.get("capability", row.capability),
            "arguments": args.get("arguments", row.arguments),
            "schedule": args.get("schedule", row.schedule),
            "timezone": args.get("timezone", row.timezone),
        }
        capability, arguments = _validate_target(merged)
        timezone_name = str(merged["timezone"])
        schedule = validate_schedule(merged["schedule"], timezone_name)
        row.name = str(merged["name"]).strip()[:256]
        row.capability = capability
        row.arguments = arguments
        row.argument_digest = canonical_digest(
            {"capability": capability, "arguments": arguments}
        )
        row.schedule = schedule
        row.timezone = timezone_name
        row.version += 1
        row.enabled = True
        row.next_run_at = compute_next_run(schedule, timezone_name)
    else:
        raise ScheduleError("unsupported schedule action")
    row.approved_action_id = action.id
    row.updated_at = datetime.now(timezone.utc)
    return {"schedule_id": str(row.id), "version": row.version, "enabled": row.enabled}


def _validate_target(args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    capability = str(args.get("capability") or "")
    arguments = args.get("arguments")
    if capability not in SCHEDULABLE_CAPABILITIES or not isinstance(arguments, dict):
        raise ScheduleError("schedule target is not allowed")
    expected = {"name"}
    if set(arguments) != expected or not str(arguments.get("name") or "").strip():
        raise ScheduleError("scheduled dataset action requires only name")
    return capability, {"name": str(arguments["name"]).strip()}


def _uuid_arg(args: dict[str, Any], name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(args[name]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ScheduleError(f"invalid {name}") from exc


class OmniAgentScheduleDispatcher:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if not settings.omniagent.product_plane_enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="omniagent-schedule-dispatcher")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                handled = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("OmniAgent schedule dispatch failed")
                handled = False
            if not handled:
                await asyncio.sleep(5)

    async def run_once(self) -> bool:
        now = datetime.now(timezone.utc)
        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(OmniAgentScheduleRow)
                    .where(
                        OmniAgentScheduleRow.enabled.is_(True),
                        OmniAgentScheduleRow.next_run_at.isnot(None),
                        OmniAgentScheduleRow.next_run_at <= now,
                    )
                    .order_by(OmniAgentScheduleRow.next_run_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            due_at = row.next_run_at
            idempotency_key = f"schedule:{row.id}:v{row.version}:{due_at.isoformat()}"
            action = OmniAgentActionRow(
                tenant_id=row.tenant_id,
                requested_by=row.owner_id,
                capability=row.capability,
                arguments=row.arguments,
                argument_digest=row.argument_digest,
                risk="R2",
                impact_preview={"schedule_id": str(row.id), "scheduled_for": due_at.isoformat()},
                state="approved",
                idempotency_key=idempotency_key[:128],
                approved_by=row.owner_id,
                approved_at=now,
                expires_at=now + timedelta(minutes=15),
            )
            db.add(action)
            await db.flush()
            job = await create_job(
                db,
                tenant_id=row.tenant_id,
                user_id=row.owner_id,
                kind="action.execute",
                spec={"action_id": str(action.id), "schedule_id": str(row.id)},
                action_id=action.id,
                max_attempts=3,
            )
            action.job_id = job.id
            row.last_run_at = now
            row.next_run_at = compute_next_run(row.schedule, row.timezone, now=now)
            if row.schedule.get("kind") == "once":
                row.enabled = False
            await append_event(
                db,
                tenant_id=row.tenant_id,
                user_id=row.owner_id,
                event_type="schedule.triggered",
                entity_type="schedule",
                entity_id=str(row.id),
                payload={"job_id": str(job.id), "version": row.version},
            )
            await db.commit()
            return True
