from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.auth.dependencies import get_current_user, require_internal
from agent_eval.config import settings
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import DatasetMetadataRow, UserRow
from agent_eval.governance.audit import AuditService
from agent_eval.governance.dedup import DedupService, DedupStrategy
from agent_eval.governance.lifecycle import DatasetStatus, LifecycleConfig, LifecycleService, RetentionPolicy
from agent_eval.governance.validator import ExampleValidator

router = APIRouter(prefix="/api", tags=["governance"], dependencies=[Depends(require_internal())])


# --- Schemas ---


class AuditLogResponse(BaseModel):
    id: str
    entity_type: str
    entity_id: str
    action: str
    user_id: str | None = None
    details: dict[str, Any] | None = None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int


class DeduplicateRequest(BaseModel):
    strategy: str = "skip"


class DeduplicateResponse(BaseModel):
    total: int
    skipped: int
    replaced: int
    suffixed: int
    passed: int
    duplicates: list[dict[str, Any]]


class DuplicateInfo(BaseModel):
    fingerprint: str
    count: int
    example_ids: list[str]


class QualityReportResponse(BaseModel):
    total: int
    valid: int
    needs_review: int
    issues_by_field: dict[str, int]
    results: list[dict[str, Any]]


class DatasetStatusResponse(BaseModel):
    dataset_name: str
    status: str
    message: str


class CapacityResponse(BaseModel):
    dataset_name: str
    current_count: int
    max_count: int
    usage_ratio: float
    warning: bool


# --- Dependencies ---


async def _get_db():
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# --- Audit Endpoints ---


@router.get("/audit", response_model=AuditLogListResponse)
async def query_audit_logs(
    entity_type: str | None = Query(None),
    entity_id: str | None = Query(None),
    action: str | None = Query(None),
    since: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(_get_db),
    user: UserRow | None = Depends(get_current_user),
):
    audit = AuditService(db)
    logs = await audit.query(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        since=since,
        limit=limit,
        offset=offset,
    )
    total = await audit.count(entity_type=entity_type, entity_id=entity_id, action=action)
    return AuditLogListResponse(
        items=[
            AuditLogResponse(
                id=str(log.id),
                entity_type=log.entity_type,
                entity_id=log.entity_id,
                action=log.action,
                user_id=str(log.user_id) if log.user_id else None,
                details=log.details,
                created_at=log.created_at,
            )
            for log in logs
        ],
        total=total,
    )


# --- Dataset Lifecycle Endpoints ---


async def _get_or_create_dataset_metadata(
    db: AsyncSession, dataset_name: str
) -> DatasetMetadataRow:
    from sqlalchemy import select
    stmt = select(DatasetMetadataRow).where(DatasetMetadataRow.dataset_name == dataset_name)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        row = DatasetMetadataRow(dataset_name=dataset_name, status="active")
        db.add(row)
        await db.flush()
    return row


@router.post("/datasets/{name}/archive", response_model=DatasetStatusResponse)
async def archive_dataset(
    name: str,
    db: AsyncSession = Depends(_get_db),
    user: UserRow | None = Depends(get_current_user),
):
    meta = await _get_or_create_dataset_metadata(db, name)
    previous_status = meta.status
    meta.status = DatasetStatus.ARCHIVED.value
    from datetime import datetime, timezone
    meta.updated_at = datetime.now(timezone.utc)

    audit = AuditService(db)
    user_id = user.id if user else None
    await audit.log(
        entity_type="dataset",
        entity_id=name,
        action="archive",
        user_id=user_id,
        details={"previous_status": previous_status, "new_status": "archived"},
    )
    return DatasetStatusResponse(
        dataset_name=name,
        status=DatasetStatus.ARCHIVED.value,
        message=f"Dataset '{name}' has been archived. No new examples can be imported.",
    )


@router.post("/datasets/{name}/activate", response_model=DatasetStatusResponse)
async def activate_dataset(
    name: str,
    db: AsyncSession = Depends(_get_db),
    user: UserRow | None = Depends(get_current_user),
):
    meta = await _get_or_create_dataset_metadata(db, name)
    previous_status = meta.status
    meta.status = DatasetStatus.ACTIVE.value
    from datetime import datetime, timezone
    meta.updated_at = datetime.now(timezone.utc)

    audit = AuditService(db)
    user_id = user.id if user else None
    await audit.log(
        entity_type="dataset",
        entity_id=name,
        action="activate",
        user_id=user_id,
        details={"previous_status": previous_status, "new_status": "active"},
    )
    return DatasetStatusResponse(
        dataset_name=name,
        status=DatasetStatus.ACTIVE.value,
        message=f"Dataset '{name}' has been activated.",
    )


# --- Dedup Endpoints ---


@router.get("/datasets/{name}/duplicates", response_model=list[DuplicateInfo])
async def get_duplicates(
    name: str,
    db: AsyncSession = Depends(_get_db),
    user: UserRow | None = Depends(get_current_user),
):
    dedup = DedupService(db)
    duplicates = await dedup.find_duplicates(name)
    return [DuplicateInfo(**d) for d in duplicates]


@router.post("/datasets/{name}/deduplicate", response_model=DeduplicateResponse)
async def deduplicate_dataset(
    name: str,
    request: DeduplicateRequest | None = None,
    db: AsyncSession = Depends(_get_db),
    user: UserRow | None = Depends(get_current_user),
):
    strategy_str = request.strategy if request else settings.governance.dedup_strategy
    try:
        strategy = DedupStrategy(strategy_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid strategy: {strategy_str}. Must be one of: skip, replace, append_suffix",
        )

    dedup = DedupService(db)
    duplicates = await dedup.find_duplicates(name)
    removed_count = 0

    if strategy == DedupStrategy.SKIP:
        removed_ids = await dedup.remove_duplicates(name)
        removed_count = len(removed_ids)

    audit = AuditService(db)
    user_id = user.id if user else None
    await audit.log(
        entity_type="dataset",
        entity_id=name,
        action="deduplicate",
        user_id=user_id,
        details={"strategy": strategy_str, "duplicates_found": len(duplicates), "removed": removed_count},
    )

    total_dups = sum(d["count"] - 1 for d in duplicates)
    return DeduplicateResponse(
        total=sum(d["count"] for d in duplicates),
        skipped=total_dups if strategy == DedupStrategy.SKIP else 0,
        replaced=total_dups if strategy == DedupStrategy.REPLACE else 0,
        suffixed=total_dups if strategy == DedupStrategy.APPEND_SUFFIX else 0,
        passed=len(duplicates),
        duplicates=duplicates,
    )


# --- Quality Endpoints ---


@router.get("/datasets/{name}/quality", response_model=QualityReportResponse)
async def get_quality_report(
    name: str,
    db: AsyncSession = Depends(_get_db),
    user: UserRow | None = Depends(get_current_user),
):
    from agent_eval.api.dependencies import get_manager

    manager = await get_manager()
    cases = await manager.provider.load_cases(name)

    validator_config = {
        "require_expected_output": settings.governance.require_expected_output,
        "max_messages": settings.governance.max_messages_per_example,
    }
    validator = ExampleValidator(config=validator_config)

    examples = []
    for case in cases:
        example_dict = {
            "name": getattr(case, "name", ""),
            "input_messages": getattr(case, "input_messages", []),
            "expected_output": getattr(case, "expected_output", None),
            "expected_output_criteria": getattr(case, "expected_output_criteria", []),
            "expected_tool_calls": getattr(case, "expected_tool_calls", []),
        }
        examples.append(example_dict)

    report = validator.validate_batch(examples)
    return QualityReportResponse(
        total=report.total,
        valid=report.valid,
        needs_review=report.needs_review,
        issues_by_field=report.issues_by_field,
        results=report.results,
    )


# --- Capacity Endpoint ---


@router.get("/datasets/{name}/capacity", response_model=CapacityResponse)
async def get_dataset_capacity(
    name: str,
    db: AsyncSession = Depends(_get_db),
    user: UserRow | None = Depends(get_current_user),
):
    lifecycle = LifecycleService(db)
    config = LifecycleConfig(
        max_examples=settings.governance.max_examples_per_dataset,
        retention_policy=RetentionPolicy(settings.governance.retention_policy),
        capacity_warning_threshold=settings.governance.capacity_warning_threshold,
    )
    capacity = await lifecycle.check_capacity(name, config)
    return CapacityResponse(
        dataset_name=name,
        current_count=capacity.current_count,
        max_count=capacity.max_count,
        usage_ratio=capacity.usage_ratio,
        warning=capacity.warning,
    )
