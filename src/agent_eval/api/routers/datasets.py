from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from agent_eval.api.dependencies import get_manager
from agent_eval.api.schemas import (
    DEFAULT_DATASET_TYPE,
    CreateDatasetRequest,
    DatasetResponse,
    DatasetStatsResponse,
    VersionResponse,
)
from agent_eval.auth.dependencies import ROLE_ADMIN, require_internal, require_role
from agent_eval.data.dataset_manager import DatasetManager
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import CandidateCaseRow, DatasetMetadataRow
from agent_eval.governance.helpers import log_audit

# All dataset endpoints require an internal role (admin|user); external_customer → 403.
router = APIRouter(
    prefix="/api/datasets",
    tags=["datasets"],
    dependencies=[Depends(require_internal())],
)


async def _get_source_project(dataset_name: str) -> str | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(DatasetMetadataRow.source_project).where(
                DatasetMetadataRow.dataset_name == dataset_name
            )
        )
        row = result.scalar_one_or_none()
        return row


async def _get_source_projects(dataset_names: list[str]) -> dict[str, str | None]:
    """Batch fetch source_project for multiple datasets."""
    if not dataset_names:
        return {}
    async with async_session_factory() as session:
        result = await session.execute(
            select(DatasetMetadataRow.dataset_name, DatasetMetadataRow.source_project)
            .where(DatasetMetadataRow.dataset_name.in_(dataset_names))
        )
        return {name: sp for name, sp in result.all()}


async def _get_dataset_types(dataset_names: list[str]) -> dict[str, str]:
    """Batch fetch dataset_type. 本地表是权威过滤源：没有行的老数据集按
    DEFAULT_DATASET_TYPE（candidate）处理，保证历史数据继续留在备选页、不丢。"""
    if not dataset_names:
        return {}
    async with async_session_factory() as session:
        result = await session.execute(
            select(DatasetMetadataRow.dataset_name, DatasetMetadataRow.dataset_type)
            .where(DatasetMetadataRow.dataset_name.in_(dataset_names))
        )
        return {name: dt for name, dt in result.all()}


async def _get_dataset_type(dataset_name: str) -> str:
    types = await _get_dataset_types([dataset_name])
    return types.get(dataset_name, DEFAULT_DATASET_TYPE)


async def _set_dataset_type(dataset_name: str, dataset_type: str) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(DatasetMetadataRow).where(
                DatasetMetadataRow.dataset_name == dataset_name
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.dataset_type = dataset_type
        else:
            session.add(DatasetMetadataRow(
                dataset_name=dataset_name,
                dataset_type=dataset_type,
            ))
        await session.commit()


async def _candidate_counts(dataset_names: list[str]) -> dict[str, int]:
    """Count candidate_cases per dataset_name. Returns {} for empty input."""
    if not dataset_names:
        return {}
    async with async_session_factory() as session:
        result = await session.execute(
            select(CandidateCaseRow.dataset_name, func.count(CandidateCaseRow.id))
            .where(CandidateCaseRow.dataset_name.in_(dataset_names))
            .group_by(CandidateCaseRow.dataset_name)
        )
        return {name: count for name, count in result.all()}


async def _candidate_count(dataset_name: str) -> int:
    counts = await _candidate_counts([dataset_name])
    return counts.get(dataset_name, 0)


async def _set_source_project(dataset_name: str, source_project: str | None) -> None:
    if source_project is None:
        return
    async with async_session_factory() as session:
        result = await session.execute(
            select(DatasetMetadataRow).where(
                DatasetMetadataRow.dataset_name == dataset_name
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.source_project = source_project
        else:
            session.add(DatasetMetadataRow(
                dataset_name=dataset_name,
                source_project=source_project,
            ))
        await session.commit()


@router.post("", response_model=dict)
async def create_dataset(
    req: CreateDatasetRequest,
    mgr: DatasetManager = Depends(get_manager),
):
    ds_id = await mgr.create_dataset(req.name, req.description, req.metadata)
    if req.source_project:
        await _set_source_project(req.name, req.source_project)
    # 始终落一行 dataset_type，使该数据集在对应页面可见、与另一类隔离。
    await _set_dataset_type(req.name, req.dataset_type)
    await log_audit("dataset", req.name, "create", details={"id": ds_id, "type": req.dataset_type})
    return {"id": ds_id, "name": req.name}


@router.get("", response_model=list[DatasetResponse])
async def list_datasets(
    filter: str | None = Query(None, description="Filter by name"),
    type: str | None = Query(
        None,
        description="按数据集类型过滤：candidate / conversation。不传=全部（兼容旧调用方）",
    ),
    mgr: DatasetManager = Depends(get_manager),
):
    datasets = await mgr.list_datasets(filter)
    names = [ds.name for ds in datasets]
    counts = await _candidate_counts(names)
    sources = await _get_source_projects(names)
    types = await _get_dataset_types(names)
    items = [
        DatasetResponse(
            id=ds.id, name=ds.name, description=ds.description,
            example_count=counts.get(ds.name, 0), created_at=ds.created_at,
            metadata=ds.metadata, source_project=sources.get(ds.name),
            dataset_type=types.get(ds.name, DEFAULT_DATASET_TYPE),
        )
        for ds in datasets
    ]
    # type 过滤在本地表权威映射之上做（老数据集无行 → DEFAULT_DATASET_TYPE）。
    if type:
        items = [d for d in items if d.dataset_type == type]
    return items


@router.get("/{name}", response_model=DatasetResponse)
async def get_dataset(
    name: str,
    mgr: DatasetManager = Depends(get_manager),
):
    try:
        ds = await mgr.get_dataset(name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Dataset '{name}' not found") from e
    sp = await _get_source_project(name)
    count = await _candidate_count(name)
    dt = await _get_dataset_type(name)
    return DatasetResponse(
        id=ds.id, name=ds.name, description=ds.description,
        example_count=count, created_at=ds.created_at,
        metadata=ds.metadata, source_project=sp,
        dataset_type=dt,
    )


@router.delete("/{name}", dependencies=[Depends(require_role(ROLE_ADMIN))])
async def delete_dataset(
    name: str,
    mgr: DatasetManager = Depends(get_manager),
):
    await mgr.delete_dataset(name)
    await log_audit("dataset", name, "delete")
    return {"deleted": name}


@router.get("/{name}/stats", response_model=DatasetStatsResponse)
async def get_stats(
    name: str,
    split: str | None = Query(None),
    tag: list[str] | None = Query(None),
    mgr: DatasetManager = Depends(get_manager),
):
    try:
        stats = await mgr.get_stats(
            name, splits=[split] if split else None, tags=tag
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LangSmith API error: {e}") from e
    return DatasetStatsResponse(
        total_cases=stats.total_cases,
        by_source=stats.by_source,
        by_tag=stats.by_tag,
        has_expected_output=stats.has_expected_output,
        has_criteria=stats.has_criteria,
        has_tool_calls=stats.has_tool_calls,
        avg_messages_per_case=stats.avg_messages_per_case,
    )


@router.get("/{name}/export")
async def export_cases(
    name: str,
    split: str | None = Query(None),
    tag: list[str] | None = Query(None),
    as_of: str | None = Query(None),
    mgr: DatasetManager = Depends(get_manager),
):
    as_of_dt = datetime.fromisoformat(as_of) if as_of else None
    data = await mgr.export_cases(
        name, as_of=as_of_dt, splits=[split] if split else None, tags=tag
    )
    return data


@router.get("/{name}/versions", response_model=list[VersionResponse])
async def list_versions(
    name: str,
    mgr: DatasetManager = Depends(get_manager),
):
    versions = await mgr.list_versions(name)
    return [
        VersionResponse(version_id=v.version_id, created_at=v.created_at)
        for v in versions
    ]
