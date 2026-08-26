from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from agent_eval.api.routers.omniagent_internal import require_scope
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import OmniAgentQuotaLedgerRow, UserRow
from agent_eval.omniagent_data.catalog import describe_entities, search_catalog
from agent_eval.omniagent_data.models import (
    DataCapabilityError,
    DescribeRequest,
    QueryRequest,
    SearchRequest,
)
from agent_eval.omniagent_data.query import execute_query
from agent_eval.omniagent_runtime.events import append_event
from agent_eval.omniagent_runtime.quota import add_quota_entry
from agent_eval.omniagent_runtime.security import ExecutionPrincipal

router = APIRouter(
    prefix="/internal/omniagent-data/v1",
    tags=["omniagent-data-internal"],
    include_in_schema=False,
)


def _error(exc: DataCapabilityError) -> HTTPException:
    status = 404 if exc.code == "ENTITY_NOT_FOUND" else 400
    if exc.code in {"QUERY_TIMEOUT", "SOURCE_UNAVAILABLE"}:
        status = 503
    return HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message})


@router.post("/search")
async def data_search(
    body: SearchRequest,
    principal: ExecutionPrincipal = Depends(require_scope("data:search")),
):
    return search_catalog(body)


@router.post("/describe")
async def data_describe(
    body: DescribeRequest,
    principal: ExecutionPrincipal = Depends(require_scope("data:describe")),
):
    try:
        return describe_entities(body)
    except DataCapabilityError as exc:
        raise _error(exc) from exc


async def _reserve_query(principal: ExecutionPrincipal) -> None:
    maximum = int(principal.budgets.get("data_queries", 0))
    if maximum <= 0:
        raise DataCapabilityError("QUERY_LIMIT", "data query budget is exhausted")
    async with async_session_factory() as db:
        user = (
            await db.execute(
                select(UserRow.id)
                .where(
                    UserRow.id == principal.user_id,
                    UserRow.tenant_id == principal.tenant_id,
                    UserRow.is_active.is_(True),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if user is None:
            raise DataCapabilityError("FORBIDDEN", "execution identity is unavailable")
        used = int(
            (
                await db.execute(
                    select(func.coalesce(func.sum(OmniAgentQuotaLedgerRow.amount), 0)).where(
                        OmniAgentQuotaLedgerRow.tenant_id == principal.tenant_id,
                        OmniAgentQuotaLedgerRow.user_id == principal.user_id,
                        OmniAgentQuotaLedgerRow.metric == "data_queries",
                        OmniAgentQuotaLedgerRow.resource_type == "execution_token",
                        OmniAgentQuotaLedgerRow.resource_id == str(principal.token_id),
                    )
                )
            ).scalar_one()
        )
        if used >= maximum:
            raise DataCapabilityError("QUERY_LIMIT", "data query budget is exhausted")
        add_quota_entry(
            db,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            metric="data_queries",
            amount=1,
            entry_type="usage",
            resource_type="execution_token",
            resource_id=str(principal.token_id),
            details={"session_id": str(principal.session_id)},
        )
        await db.commit()


@router.post("/query")
async def data_query(
    body: QueryRequest,
    principal: ExecutionPrincipal = Depends(require_scope("data:query")),
):
    try:
        await _reserve_query(principal)
        async with async_session_factory() as db:
            response = await execute_query(
                db,
                request=body,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=principal.session_id,
                message_id=principal.message_id,
            )
            await db.rollback()
        audit = response.pop("_audit")
        async with async_session_factory() as db:
            await append_event(
                db,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=principal.session_id,
                message_id=principal.message_id,
                event_type="data.query.completed",
                entity_type="data_query",
                entity_id=response["query_id"],
                payload=audit,
            )
            await db.commit()
        return response
    except DataCapabilityError as exc:
        raise _error(exc) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_ARGUMENT", "message": str(exc)},
        ) from exc
