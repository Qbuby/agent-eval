from __future__ import annotations

import asyncio
import logging
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

logger = logging.getLogger(__name__)


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
    # 仅由 create_dataset 调用：落 dataset_type 的同时把 status 复位为 active。
    # 这一步对「软删除后同名重建」是必需的——Langfuse 的 create_dataset 是幂等
    # upsert（同名合法），但本地若残留 status=deleted 的旧行，list 会永久过滤掉
    # 重建后的数据集，UI 上表现为「建了却看不见」。创建动作本身即意味 active。
    async with async_session_factory() as session:
        result = await session.execute(
            select(DatasetMetadataRow).where(
                DatasetMetadataRow.dataset_name == dataset_name
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.dataset_type = dataset_type
            row.status = "active"
        else:
            session.add(DatasetMetadataRow(
                dataset_name=dataset_name,
                dataset_type=dataset_type,
            ))
        await session.commit()


async def _mark_dataset_deleted(dataset_name: str) -> None:
    """软删除：本地 dataset_metadata 标记 status=deleted（Langfuse 无删库 API）。
    无本地行的老数据集补一行，确保后续 list 能过滤掉它。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(DatasetMetadataRow).where(
                DatasetMetadataRow.dataset_name == dataset_name
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.status = "deleted"
        else:
            session.add(DatasetMetadataRow(
                dataset_name=dataset_name,
                status="deleted",
            ))
        await session.commit()


async def _get_deleted_names(dataset_names: list[str]) -> set[str]:
    """返回这批数据集里被软删除（status=deleted）的名字集合。"""
    if not dataset_names:
        return set()
    async with async_session_factory() as session:
        result = await session.execute(
            select(DatasetMetadataRow.dataset_name)
            .where(DatasetMetadataRow.dataset_name.in_(dataset_names))
            .where(DatasetMetadataRow.status == "deleted")
        )
        return {name for (name,) in result.all()}


async def _ensure_not_deleted(dataset_name: str) -> None:
    """该数据集若已被软删除（status=deleted）则抛 404。单点端点用它对已删名字
    返回 404，与 list_datasets 的过滤保持一致——否则 deep-link 仍能读到「已删」的库。"""
    if await _get_deleted_names([dataset_name]):
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_name}' not found")


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
    # 软删除：Langfuse 删不掉整库，被删数据集在本地标 status=deleted，这里过滤掉。
    deleted = await _get_deleted_names([ds.name for ds in datasets])
    datasets = [ds for ds in datasets if ds.name not in deleted]
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

    # conversation 样例存在 provider（Langfuse/LangSmith dataset items），不落
    # candidate_cases 本地表，故 _candidate_counts 对其恒为 0。这里对 conversation
    # 数据集改用 provider 真实 item 计数。逐个 load 有开销，但 conversation 列表
    # 通常远少于 candidate，且仅在按 conversation 过滤或混合列表里命中。
    conv_items = [d for d in items if d.dataset_type == "conversation"]
    if conv_items:
        async def _real_count(ds_name: str) -> int:
            try:
                cases = await mgr.load_cases(ds_name)
                return len(cases)
            except Exception:
                return 0
        real_counts = await asyncio.gather(*[_real_count(d.name) for d in conv_items])
        for d, c in zip(conv_items, real_counts):
            d.example_count = c
    return items


@router.get("/{name}", response_model=DatasetResponse)
async def get_dataset(
    name: str,
    mgr: DatasetManager = Depends(get_manager),
):
    await _ensure_not_deleted(name)
    try:
        ds = await mgr.get_dataset(name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Dataset '{name}' not found") from e
    sp = await _get_source_project(name)
    dt = await _get_dataset_type(name)
    # conversation：用 provider 真实 item 计数（get_dataset 已算 len(items)）；
    # candidate：用本地 candidate_cases 计数。
    count = ds.example_count if dt == "conversation" else await _candidate_count(name)
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
    # Langfuse 没有删库 API：provider.delete_dataset 清空 items，再在本地
    # dataset_metadata 标记 status=deleted，list 时过滤，UI 上等价于删除。
    # provider 清空失败（云端数据集缺失/漂移）不应吞掉用户的软删意图——
    # 无论如何都要落地本地软删标记，保证 list 一致地隐藏它。
    try:
        await mgr.delete_dataset(name)
    except Exception:
        logger.warning("delete_dataset: provider 清空 '%s' 失败，仍执行本地软删", name)
    await _mark_dataset_deleted(name)
    await log_audit("dataset", name, "delete")
    return {"deleted": name}


@router.get("/{name}/stats", response_model=DatasetStatsResponse)
async def get_stats(
    name: str,
    split: str | None = Query(None),
    tag: list[str] | None = Query(None),
    mgr: DatasetManager = Depends(get_manager),
):
    await _ensure_not_deleted(name)
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
    await _ensure_not_deleted(name)
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
    await _ensure_not_deleted(name)
    versions = await mgr.list_versions(name)
    return [
        VersionResponse(version_id=v.version_id, created_at=v.created_at)
        for v in versions
    ]
