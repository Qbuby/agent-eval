from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agent_eval.auth.dependencies import get_current_user
from agent_eval.db import get_session
from agent_eval.db_models.repository import Repository
from agent_eval.db_models.tables import UserRow
from agent_eval.governance.helpers import log_audit

router = APIRouter(prefix="/api/routing", tags=["routing"])


class RoutingConditions(BaseModel):
    tags: list[str] | None = None
    metadata_match: dict[str, Any] | None = None
    status: str | None = None
    min_duration_ms: int | None = None


class TransformConfig(BaseModel):
    include_output_as_expected: bool = False
    default_tags: list[str] = []
    split: str | None = None


class CreateRuleRequest(BaseModel):
    name: str
    priority: int = 100
    source_project: str
    conditions: RoutingConditions = RoutingConditions()
    target_dataset: str
    transform_config: TransformConfig = TransformConfig()
    is_active: bool = True


class UpdateRuleRequest(BaseModel):
    name: str | None = None
    priority: int | None = None
    source_project: str | None = None
    conditions: RoutingConditions | None = None
    target_dataset: str | None = None
    transform_config: TransformConfig | None = None
    is_active: bool | None = None


class RuleResponse(BaseModel):
    id: str
    name: str
    priority: int
    source_project: str
    conditions: dict
    target_dataset: str
    transform_config: dict
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TestRuleRequest(BaseModel):
    run: dict[str, Any]
    project_name: str


class RoutingLogResponse(BaseModel):
    id: str
    rule_id: str | None
    run_id: str
    source_project: str
    target_dataset: str | None
    status: str
    error_message: str | None
    created_at: datetime | None = None


class PaginatedLogsResponse(BaseModel):
    items: list[RoutingLogResponse]
    total: int
    limit: int
    offset: int


class RoutingStatsResponse(BaseModel):
    rule_id: str | None
    total: int
    routed: int
    failed: int
    skipped: int


def _rule_to_response(row) -> RuleResponse:
    return RuleResponse(
        id=str(row.id),
        name=row.name,
        priority=row.priority,
        source_project=row.source_project,
        conditions=row.conditions or {},
        target_dataset=row.target_dataset,
        transform_config=row.transform_config or {},
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/rules", response_model=list[RuleResponse])
async def list_rules(_user: UserRow | None = Depends(get_current_user)):
    async with get_session() as session:
        repo = Repository(session)
        rules = await repo.list_routing_rules()
        return [_rule_to_response(r) for r in rules]


@router.post("/rules", response_model=RuleResponse, status_code=201)
async def create_rule(req: CreateRuleRequest, user: UserRow | None = Depends(get_current_user)):
    async with get_session() as session:
        repo = Repository(session)
        row = await repo.create_routing_rule(
            name=req.name,
            priority=req.priority,
            source_project=req.source_project,
            conditions=req.conditions.model_dump(exclude_none=True),
            target_dataset=req.target_dataset,
            transform_config=req.transform_config.model_dump(exclude_none=True),
            is_active=req.is_active,
            created_by=user.id if user else None,
        )
        await log_audit("rule", str(row.id), "create", user_id=user.id if user else None, details={"name": req.name, "target": req.target_dataset})
        return _rule_to_response(row)


@router.put("/rules/{rule_id}", response_model=RuleResponse)
async def update_rule(rule_id: uuid.UUID, req: UpdateRuleRequest, user: UserRow | None = Depends(get_current_user)):
    updates = {}
    if req.name is not None:
        updates["name"] = req.name
    if req.priority is not None:
        updates["priority"] = req.priority
    if req.source_project is not None:
        updates["source_project"] = req.source_project
    if req.conditions is not None:
        updates["conditions"] = req.conditions.model_dump(exclude_none=True)
    if req.target_dataset is not None:
        updates["target_dataset"] = req.target_dataset
    if req.transform_config is not None:
        updates["transform_config"] = req.transform_config.model_dump(exclude_none=True)
    if req.is_active is not None:
        updates["is_active"] = req.is_active

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    async with get_session() as session:
        repo = Repository(session)
        row = await repo.update_routing_rule(rule_id, **updates)
        if row is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        await log_audit("rule", str(rule_id), "update", user_id=user.id if user else None, details=updates)
        return _rule_to_response(row)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: uuid.UUID, user: UserRow | None = Depends(get_current_user)):
    async with get_session() as session:
        repo = Repository(session)
        deleted = await repo.delete_routing_rule(rule_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Rule not found")
        await log_audit("rule", str(rule_id), "delete", user_id=user.id if user else None)


@router.post("/rules/{rule_id}/test")
async def test_rule(rule_id: uuid.UUID, req: TestRuleRequest, _user: UserRow | None = Depends(get_current_user)):
    async with get_session() as session:
        repo = Repository(session)
        rule = await repo.get_routing_rule(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found")

    from agent_eval.routing.matcher import RuleMatcher
    matcher = RuleMatcher()
    matched = matcher.matches(rule, req.run, req.project_name)
    return {"matched": matched, "rule_id": str(rule_id), "target_dataset": rule.target_dataset if matched else None}


@router.get("/logs", response_model=PaginatedLogsResponse)
async def list_logs(
    source_project: str | None = None,
    target_dataset: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    _user: UserRow | None = Depends(get_current_user),
):
    async with get_session() as session:
        repo = Repository(session)
        logs, total = await repo.list_routing_logs(
            source_project=source_project,
            target_dataset=target_dataset,
            status=status,
            limit=limit,
            offset=offset,
        )
        items = [
            RoutingLogResponse(
                id=str(log.id),
                rule_id=str(log.rule_id) if log.rule_id else None,
                run_id=log.run_id,
                source_project=log.source_project,
                target_dataset=log.target_dataset,
                status=log.status,
                error_message=log.error_message,
                created_at=log.created_at,
            )
            for log in logs
        ]
        return PaginatedLogsResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/stats", response_model=list[RoutingStatsResponse])
async def get_stats(_user: UserRow | None = Depends(get_current_user)):
    async with get_session() as session:
        repo = Repository(session)
        stats = await repo.get_routing_stats()
        return [RoutingStatsResponse(**s) for s in stats]
