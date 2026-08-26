from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db_models.tables import OmniAgentArtifactRow, OmniAgentJobRow
from agent_eval.omniagent_runtime.artifacts import owner_filter
from agent_eval.omniagent_runtime.jobs import create_job
from agent_eval.omniagent_runtime.quota import add_quota_entry, reserve_analysis_slot


async def submit_analysis(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    code: str,
    artifact_ids: list[uuid.UUID],
) -> OmniAgentJobRow:
    if not code.strip():
        raise ValueError("analysis code is required")
    if len(code.encode("utf-8")) > 1024 * 1024:
        raise ValueError("analysis code exceeds 1 MiB")
    unique_ids = list(dict.fromkeys(artifact_ids))
    if len(unique_ids) > 20:
        raise ValueError("analysis accepts at most 20 input artifacts")
    await reserve_analysis_slot(db, tenant_id=tenant_id, user_id=user_id)
    if unique_ids:
        rows = list(
            (
                await db.execute(
                    select(OmniAgentArtifactRow.id).where(
                        OmniAgentArtifactRow.id.in_(unique_ids),
                        OmniAgentArtifactRow.tenant_id == tenant_id,
                        owner_filter(OmniAgentArtifactRow.owner_id, user_id),
                        OmniAgentArtifactRow.state == "available",
                    )
                )
            ).scalars()
        )
        if set(rows) != set(unique_ids):
            raise ValueError("one or more input artifacts are unavailable")
    job = await create_job(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        kind="analysis.python",
        spec={
            "code": code,
            "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "artifact_ids": [str(item) for item in unique_ids],
        },
        session_id=session_id,
        message_id=message_id,
        max_attempts=3,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    add_quota_entry(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        metric="analysis_jobs",
        amount=1,
        entry_type="reservation",
        resource_type="job",
        resource_id=str(job.id),
        details={"expires_at": job.expires_at.isoformat()},
    )
    return job
