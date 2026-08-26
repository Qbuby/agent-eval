from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db_models.tables import OmniAgentActionRow
from agent_eval.omniagent_runtime.events import append_event
from agent_eval.omniagent_runtime.jobs import create_job
from agent_eval.omniagent_runtime.security import canonical_digest
from agent_eval.omniagent_runtime.state import ACTION_TRANSITIONS, require_transition


@dataclass(frozen=True)
class CapabilityDefinition:
    name: str
    risk: str
    preview: Callable[[dict[str, Any]], dict[str, Any]]


def _keys(required: set[str], optional: set[str] | None = None):
    allowed_optional = optional or set()
    def validate(arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        missing = required - arguments.keys()
        unknown = arguments.keys() - required - allowed_optional
        if missing:
            raise ValueError(f"missing arguments: {', '.join(sorted(missing))}")
        if unknown:
            raise ValueError(f"unknown arguments: {', '.join(sorted(unknown))}")
        return dict(arguments)
    return validate


_VALIDATORS = {
    "memory.save": _keys({"title", "content"}, {"tags"}),
    "memory.delete": _keys({"memory_id"}),
    "artifact.pin": _keys({"artifact_id"}),
    "dataset.archive": _keys({"name"}),
    "dataset.activate": _keys({"name"}),
    "schedule.create": _keys(
        {"name", "capability", "arguments", "schedule"}, {"timezone"}
    ),
    "schedule.update": _keys(
        {"schedule_id"}, {"name", "capability", "arguments", "schedule", "timezone"}
    ),
    "schedule.resume": _keys({"schedule_id"}),
}

CAPABILITIES = {
    name: CapabilityDefinition(
        name=name,
        risk="R2" if name.startswith(("memory.", "artifact.", "dataset.")) else "R3",
        preview=lambda args, capability=name: {"capability": capability, "arguments": args},
    )
    for name in _VALIDATORS
}


def action_dict(row: OmniAgentActionRow) -> dict[str, Any]:
    return {
        "id": str(row.id), "capability": row.capability, "arguments": row.arguments,
        "argument_digest": row.argument_digest, "risk": row.risk,
        "impact_preview": row.impact_preview, "cost_estimate": row.cost_estimate,
        "state": row.state, "expires_at": row.expires_at,
        "session_id": str(row.session_id) if row.session_id else None,
        "message_id": str(row.message_id) if row.message_id else None,
        "job_id": str(row.job_id) if row.job_id else None,
        "terminal_summary": row.terminal_summary, "created_at": row.created_at,
    }


def validate_capability(capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
    validator = _VALIDATORS.get(capability)
    if validator is None:
        raise ValueError("capability is not registered")
    normalized = validator(arguments)
    if capability == "memory.save":
        normalized["title"] = str(normalized["title"]).strip()[:256]
        normalized["content"] = str(normalized["content"]).strip()
        normalized["tags"] = [str(x)[:64] for x in normalized.get("tags", [])[:20]]
        if not normalized["title"] or not normalized["content"]:
            raise ValueError("title and content are required")
    return normalized


async def prepare_action(
    db: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID | None,
    capability: str, arguments: dict[str, Any], idempotency_key: str,
    session_id: uuid.UUID | None = None, message_id: uuid.UUID | None = None,
) -> OmniAgentActionRow:
    normalized = validate_capability(capability, arguments)
    digest = canonical_digest({"capability": capability, "arguments": normalized})
    existing = (await db.execute(select(OmniAgentActionRow).where(
        OmniAgentActionRow.tenant_id == tenant_id,
        OmniAgentActionRow.idempotency_key == idempotency_key,
    ))).scalar_one_or_none()
    if existing:
        if existing.argument_digest != digest:
            raise ValueError("idempotency key already binds different arguments")
        return existing
    definition = CAPABILITIES[capability]
    row = OmniAgentActionRow(
        tenant_id=tenant_id, requested_by=user_id, capability=capability,
        arguments=normalized, argument_digest=digest, risk=definition.risk,
        impact_preview=definition.preview(normalized), idempotency_key=idempotency_key[:128],
        session_id=session_id, message_id=message_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(row)
    await db.flush()
    await append_event(db, tenant_id=tenant_id, user_id=user_id, session_id=session_id,
        message_id=message_id, event_type="action.prepared", entity_type="action",
        entity_id=str(row.id), payload=action_dict(row))
    return row


async def decide_action(
    db: AsyncSession, *, action_id: uuid.UUID, tenant_id: uuid.UUID,
    user_id: uuid.UUID | None, digest: str, decision: str,
) -> OmniAgentActionRow | None:
    owner = OmniAgentActionRow.requested_by.is_(None) if user_id is None else OmniAgentActionRow.requested_by == user_id
    row = (await db.execute(select(OmniAgentActionRow).where(
        OmniAgentActionRow.id == action_id, OmniAgentActionRow.tenant_id == tenant_id, owner,
    ).with_for_update())).scalar_one_or_none()
    if row is None:
        return None
    if row.argument_digest != digest:
        raise ValueError("approval digest does not match")
    now = datetime.now(timezone.utc)
    if row.state != "prepared":
        return row
    if row.expires_at <= now:
        row.state = "expired"
    elif decision == "deny":
        require_transition(ACTION_TRANSITIONS, row.state, "denied")
        row.state, row.denied_at = "denied", now
    elif decision == "approve":
        require_transition(ACTION_TRANSITIONS, row.state, "approved")
        row.state, row.approved_by, row.approved_at = "approved", user_id, now
        job = await create_job(db, tenant_id=tenant_id, user_id=user_id,
            kind="action.execute", spec={"action_id": str(row.id)}, session_id=row.session_id,
            message_id=row.message_id, action_id=row.id, max_attempts=3)
        row.job_id = job.id
    else:
        raise ValueError("decision must be approve or deny")
    await append_event(db, tenant_id=tenant_id, user_id=user_id, session_id=row.session_id,
        message_id=row.message_id, event_type=f"action.{row.state}", entity_type="action",
        entity_id=str(row.id), payload=action_dict(row))
    return row
