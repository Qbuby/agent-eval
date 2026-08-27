from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete

from agent_eval.auth.security import hash_password
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    OmniAgentActionRow,
    OmniAgentArtifactRow,
    OmniAgentChatSessionRow,
    OmniAgentEventRow,
    OmniAgentJobRow,
    OmniAgentMemoryRow,
    OmniAgentNotificationRow,
    OmniAgentScheduleRow,
    TenantRow,
    UserRow,
)
from agent_eval.omniagent_runtime.security import canonical_digest


TENANTS = (
    {
        "key": "a",
        "tenant_id": uuid.UUID("10000000-0000-4000-8000-000000000101"),
        "user_id": uuid.UUID("20000000-0000-4000-8000-000000000201"),
        "session_id": uuid.UUID("30000000-0000-4000-8000-000000000301"),
        "job_id": uuid.UUID("40000000-0000-4000-8000-000000000401"),
        "prepared_action_id": uuid.UUID("50000000-0000-4000-8000-000000000501"),
        "approved_action_id": uuid.UUID("50000000-0000-4000-8000-000000000511"),
        "artifact_id": uuid.UUID("60000000-0000-4000-8000-000000000601"),
        "memory_id": uuid.UUID("70000000-0000-4000-8000-000000000701"),
        "notification_id": uuid.UUID("80000000-0000-4000-8000-000000000801"),
        "schedule_id": uuid.UUID("90000000-0000-4000-8000-000000000901"),
        "marker": "TENANT_ALPHA_ONLY",
        "username": "oa_tenant_alpha",
    },
    {
        "key": "b",
        "tenant_id": uuid.UUID("10000000-0000-4000-8000-000000000102"),
        "user_id": uuid.UUID("20000000-0000-4000-8000-000000000202"),
        "session_id": uuid.UUID("30000000-0000-4000-8000-000000000302"),
        "job_id": uuid.UUID("40000000-0000-4000-8000-000000000402"),
        "prepared_action_id": uuid.UUID("50000000-0000-4000-8000-000000000502"),
        "approved_action_id": uuid.UUID("50000000-0000-4000-8000-000000000512"),
        "artifact_id": uuid.UUID("60000000-0000-4000-8000-000000000602"),
        "memory_id": uuid.UUID("70000000-0000-4000-8000-000000000702"),
        "notification_id": uuid.UUID("80000000-0000-4000-8000-000000000802"),
        "schedule_id": uuid.UUID("90000000-0000-4000-8000-000000000902"),
        "marker": "TENANT_BETA_ONLY",
        "username": "oa_tenant_beta",
    },
)


def _fixture_payload(item: dict) -> dict:
    return {
        "username": item["username"],
        "password": item["password"],
        "tenant_id": str(item["tenant_id"]),
        "user_id": str(item["user_id"]),
        "session_id": str(item["session_id"]),
        "job_id": str(item["job_id"]),
        "action_id": str(item["prepared_action_id"]),
        "artifact_id": str(item["artifact_id"]),
        "memory_id": str(item["memory_id"]),
        "notification_id": str(item["notification_id"]),
        "schedule_id": str(item["schedule_id"]),
        "event_cursor": item["event_cursor"],
        "marker": item["marker"],
    }


async def seed() -> None:
    now = datetime.now(timezone.utc)
    artifact_root = Path("/data/artifacts")
    artifact_root.mkdir(parents=True, exist_ok=True)

    tenant_ids = [item["tenant_id"] for item in TENANTS]
    user_ids = [item["user_id"] for item in TENANTS]
    session_ids = [item["session_id"] for item in TENANTS]

    async with async_session_factory() as db:
        # The database is dedicated to this E2E, but deleting the deterministic
        # fixture first keeps retries idempotent after an interrupted run.
        for model in (
            OmniAgentNotificationRow,
            OmniAgentScheduleRow,
            OmniAgentMemoryRow,
            OmniAgentArtifactRow,
            OmniAgentEventRow,
            OmniAgentActionRow,
            OmniAgentJobRow,
        ):
            await db.execute(delete(model).where(model.tenant_id.in_(tenant_ids)))
        await db.execute(
            delete(OmniAgentChatSessionRow).where(OmniAgentChatSessionRow.id.in_(session_ids))
        )
        await db.execute(delete(UserRow).where(UserRow.id.in_(user_ids)))
        await db.execute(delete(TenantRow).where(TenantRow.id.in_(tenant_ids)))
        await db.flush()

        for item in TENANTS:
            item["password"] = f"OaE2e!{secrets.token_urlsafe(18)}"
            marker = item["marker"]
            tenant_id = item["tenant_id"]
            user_id = item["user_id"]
            session_id = item["session_id"]

            db.add(
                TenantRow(
                    id=tenant_id,
                    name=f"{marker} Organization",
                    slug=f"oa-two-tenant-{item['key']}",
                    status="active",
                )
            )
            # TenantRow and UserRow intentionally have no ORM relationships,
            # and their tables form a nullable FK cycle through created_by.
            # Flush each ownership layer before inserting rows that reference it.
            await db.flush()
            db.add(
                UserRow(
                    id=user_id,
                    username=item["username"],
                    email=f"{item['username']}@example.test",
                    hashed_password=hash_password(item["password"]),
                    role="user",
                    tenant_id=tenant_id,
                    is_superadmin=False,
                    is_active=True,
                )
            )
            await db.flush()
            db.add(
                OmniAgentChatSessionRow(
                    id=session_id,
                    tenant_id=tenant_id,
                    created_by=user_id,
                    thread_id=f"oa-two-tenant-{item['key']}-thread",
                    title=f"{marker} Session",
                    title_source="manual",
                    last_message_at=now,
                )
            )
            await db.flush()
            db.add(
                OmniAgentJobRow(
                    id=item["job_id"],
                    tenant_id=tenant_id,
                    kind=f"analysis.{marker.lower()}",
                    status="running",
                    spec={"marker": marker},
                    requested_by=user_id,
                    session_id=session_id,
                    attempt_count=1,
                    max_attempts=3,
                    started_at=now,
                    expires_at=now + timedelta(hours=1),
                )
            )

            prepared_arguments = {
                "title": f"{marker} Approval",
                "content": f"{marker} approval content",
                "tags": [marker],
            }
            prepared_digest = canonical_digest(
                {"capability": "memory.save", "arguments": prepared_arguments}
            )
            db.add(
                OmniAgentActionRow(
                    id=item["prepared_action_id"],
                    tenant_id=tenant_id,
                    capability="memory.save",
                    arguments=prepared_arguments,
                    argument_digest=prepared_digest,
                    risk="R2",
                    impact_preview={"marker": marker, "title": prepared_arguments["title"]},
                    state="prepared",
                    requested_by=user_id,
                    session_id=session_id,
                    idempotency_key=f"oa-two-tenant-{item['key']}-prepared",
                    expires_at=now + timedelta(hours=1),
                )
            )
            # Sandbox schedules reference the approved action by FK. Persist
            # both actions before adding the schedule fixture below.
            await db.flush()
            approved_arguments = {"name": f"{marker} Dataset"}
            approved_digest = canonical_digest(
                {"capability": "dataset.archive", "arguments": approved_arguments}
            )
            db.add(
                OmniAgentActionRow(
                    id=item["approved_action_id"],
                    tenant_id=tenant_id,
                    capability="dataset.archive",
                    arguments=approved_arguments,
                    argument_digest=approved_digest,
                    risk="R2",
                    impact_preview={"marker": marker},
                    state="approved",
                    requested_by=user_id,
                    approved_by=user_id,
                    approved_at=now,
                    idempotency_key=f"oa-two-tenant-{item['key']}-approved",
                    expires_at=now + timedelta(hours=1),
                )
            )

            content = f"{marker} artifact body\n".encode()
            object_key = f"{tenant_id}/{user_id}/{item['artifact_id']}/content.txt"
            artifact_path = artifact_root / object_key
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_bytes(content)
            db.add(
                OmniAgentArtifactRow(
                    id=item["artifact_id"],
                    tenant_id=tenant_id,
                    owner_id=user_id,
                    session_id=session_id,
                    state="available",
                    filename=f"{marker}.txt",
                    mime_type="text/plain",
                    extension=".txt",
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    object_key=object_key,
                    scan_result={"clean": True, "engine": "e2e-fixture"},
                    retention="temporary",
                    expires_at=now + timedelta(hours=1),
                )
            )
            db.add(
                OmniAgentMemoryRow(
                    id=item["memory_id"],
                    tenant_id=tenant_id,
                    owner_id=user_id,
                    title=f"{marker} Memory",
                    content=f"{marker} memory body",
                    tags=[marker],
                    content_digest=canonical_digest(f"{marker} memory body"),
                )
            )
            event = OmniAgentEventRow(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                event_type=f"{marker}.event",
                entity_type=f"{marker}.entity",
                entity_id=marker,
                payload={"marker": marker},
                created_at=now,
            )
            db.add(event)
            db.add(
                OmniAgentNotificationRow(
                    id=item["notification_id"],
                    tenant_id=tenant_id,
                    user_id=user_id,
                    kind="e2e",
                    title=f"{marker} Notification",
                    body=f"{marker} notification body",
                    link="/omniagent",
                )
            )
            db.add(
                OmniAgentScheduleRow(
                    id=item["schedule_id"],
                    tenant_id=tenant_id,
                    owner_id=user_id,
                    name=f"{marker} Schedule",
                    capability="dataset.archive",
                    arguments=approved_arguments,
                    argument_digest=approved_digest,
                    schedule={"kind": "interval", "seconds": 3600},
                    timezone="Asia/Shanghai",
                    version=1,
                    enabled=False,
                    approved_action_id=item["approved_action_id"],
                )
            )
            await db.flush()
            item["event_cursor"] = event.id

        await db.commit()

    print(json.dumps({item["key"]: _fixture_payload(item) for item in TENANTS}))


if __name__ == "__main__":
    asyncio.run(seed())
