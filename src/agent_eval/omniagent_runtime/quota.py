from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db_models.tables import (
    OmniAgentJobRow,
    OmniAgentQuotaLedgerRow,
    TenantRow,
    UserRow,
)
from agent_eval.omniagent_runtime.policy import DEFAULT_POLICY

ACTIVE_STATUSES = frozenset({"queued", "provisioning", "running"})


class QuotaExceeded(ValueError):
    pass


async def reserve_analysis_slot(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Serialize admission per tenant, then enforce user and tenant limits."""
    tenant = (
        await db.execute(select(TenantRow.id).where(TenantRow.id == tenant_id).with_for_update())
    ).scalar_one_or_none()
    user = (
        await db.execute(
            select(UserRow.id)
            .where(UserRow.id == user_id, UserRow.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if tenant is None or user is None:
        raise QuotaExceeded("identity is not available for quota reservation")

    user_active = int(
        (
            await db.execute(
                select(func.count())
                .select_from(OmniAgentJobRow)
                .where(
                    OmniAgentJobRow.tenant_id == tenant_id,
                    OmniAgentJobRow.requested_by == user_id,
                    OmniAgentJobRow.kind == "analysis.python",
                    OmniAgentJobRow.status.in_(ACTIVE_STATUSES),
                )
            )
        ).scalar_one()
    )
    tenant_active = int(
        (
            await db.execute(
                select(func.count())
                .select_from(OmniAgentJobRow)
                .where(
                    OmniAgentJobRow.tenant_id == tenant_id,
                    OmniAgentJobRow.kind == "analysis.python",
                    OmniAgentJobRow.status.in_(ACTIVE_STATUSES),
                )
            )
        ).scalar_one()
    )
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    user_daily = int(
        (
            await db.execute(
                select(func.count())
                .select_from(OmniAgentJobRow)
                .where(
                    OmniAgentJobRow.tenant_id == tenant_id,
                    OmniAgentJobRow.requested_by == user_id,
                    OmniAgentJobRow.kind == "analysis.python",
                    OmniAgentJobRow.created_at >= day_start,
                )
            )
        ).scalar_one()
    )
    if user_active >= DEFAULT_POLICY.analysis_concurrency_user:
        raise QuotaExceeded("QUOTA_EXCEEDED: user analysis concurrency")
    if tenant_active >= DEFAULT_POLICY.analysis_concurrency_tenant:
        raise QuotaExceeded("QUOTA_EXCEEDED: tenant analysis concurrency")
    if user_daily >= DEFAULT_POLICY.analysis_jobs_per_day_user:
        raise QuotaExceeded("QUOTA_EXCEEDED: daily analysis jobs")


def add_quota_entry(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    metric: str,
    amount: float,
    entry_type: str,
    resource_type: str,
    resource_id: str,
    details: dict | None = None,
) -> OmniAgentQuotaLedgerRow:
    now = datetime.now(timezone.utc)
    row = OmniAgentQuotaLedgerRow(
        tenant_id=tenant_id,
        user_id=user_id,
        metric=metric,
        amount=amount,
        entry_type=entry_type,
        resource_type=resource_type,
        resource_id=resource_id,
        bucket_start=now.replace(hour=0, minute=0, second=0, microsecond=0),
        details=details,
        created_at=now,
    )
    db.add(row)
    return row
