from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from agent_eval.api.dependencies import get_manager
from agent_eval.api.schemas import (
    CreateDatasetRequest,
    DatasetResponse,
    DatasetStatsResponse,
    VersionResponse,
)
from agent_eval.auth.dependencies import ROLE_ADMIN, get_current_user, require_role
from agent_eval.data.dataset_manager import DatasetManager
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import CandidateCaseRow, DatasetMetadataRow
from agent_eval.governance.helpers import log_audit

# All dataset endpoints require an authenticated user (login-only baseline).
router = APIRouter(
    prefix="/api/datasets",
    tags=["datasets"],
    dependencies=[Depends(get_current_user)],
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
    await log_audit("dataset", req.name, "create", details={"id": ds_id})
    return {"id": ds_id, "name": req.name}


@router.get("", response_model=list[DatasetResponse])
async def list_datasets(
    filter: str | None = Query(None, description="Filter by name"),
    mgr: DatasetManager = Depends(get_manager),
):
    datasets = await mgr.list_datasets(filter)
    names = [ds.name for ds in datasets]
    counts = await _candidate_counts(names)
    sources = await _get_source_projects(names)
    return [
        DatasetResponse(
            id=ds.id, name=ds.name, description=ds.description,
            example_count=counts.get(ds.name, 0), created_at=ds.created_at,
            metadata=ds.metadata, source_project=sources.get(ds.name),
        )
        for ds in datasets
    ]


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
    return DatasetResponse(
        id=ds.id, name=ds.name, description=ds.description,
        example_count=count, created_at=ds.created_at,
        metadata=ds.metadata, source_project=sp,
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
