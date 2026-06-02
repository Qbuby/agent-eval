from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.api.exporters import ExportColumn, build_export_response, validate_format
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    BenchmarkCaseRow, BenchmarkVersionRow, CandidateCaseRow, CategoryRow,
    ImportBatchRow, ProjectRow,
)
from agent_eval.data.benchmark_import import (
    auto_match_columns, get_answer_from_row, get_question_from_row,
    parse_upload_file, resolve_extra_fields,
)

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])


class BenchmarkCaseCreate(BaseModel):
    category_id: str | None = None
    question: str
    reference_answer: str | None = None
    key_points: list[str] = []
    negative_points: list[str] = []
    tags: list[str] = []
    difficulty: str | None = None


class BenchmarkCaseUpdate(BaseModel):
    category_id: str | None = None
    question: str | None = None
    reference_answer: str | None = None
    key_points: list[str] | None = None
    negative_points: list[str] | None = None
    tags: list[str] | None = None
    difficulty: str | None = None
    status: str | None = None
    extra_fields: dict | None = None


class CreateVersionRequest(BaseModel):
    version_tag: str
    description: str = ""


class SchemaConfigUpdate(BaseModel):
    schema_config: dict


@router.get("/{project_id}/cases")
async def list_benchmark_cases(
    project_id: str,
    category_id: str | None = Query(None),
    tag: str | None = Query(None),
    search: str | None = Query(None),
    status: str = Query("active"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    async with async_session_factory() as session:
        stmt = select(BenchmarkCaseRow).where(
            BenchmarkCaseRow.project_id == project_id,
            BenchmarkCaseRow.status == status,
        )
        if category_id:
            stmt = stmt.where(BenchmarkCaseRow.category_id == category_id)
        if tag:
            stmt = stmt.where(BenchmarkCaseRow.tags.any(tag))
        if search:
            stmt = stmt.where(BenchmarkCaseRow.question.ilike(f"%{search}%"))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(BenchmarkCaseRow.created_at.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await session.execute(stmt)
        cases = result.scalars().all()

    return {
        "items": [_case_to_dict(c) for c in cases],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{project_id}/cases/export")
async def export_benchmark_cases(
    project_id: str,
    category_id: str | None = Query(None),
    tag: str | None = Query(None),
    search: str | None = Query(None),
    status: str = Query("active"),
    format: str = Query("csv"),
):
    """Export all benchmark cases matching the current filters (no pagination)."""
    validate_format(format)
    async with async_session_factory() as session:
        stmt = select(BenchmarkCaseRow).where(
            BenchmarkCaseRow.project_id == project_id,
            BenchmarkCaseRow.status == status,
        )
        if category_id:
            stmt = stmt.where(BenchmarkCaseRow.category_id == category_id)
        if tag:
            stmt = stmt.where(BenchmarkCaseRow.tags.any(tag))
        if search:
            stmt = stmt.where(BenchmarkCaseRow.question.ilike(f"%{search}%"))
        stmt = stmt.order_by(BenchmarkCaseRow.created_at.desc())
        result = await session.execute(stmt)
        cases = result.scalars().all()

    rows = [_case_to_dict(c) for c in cases]
    columns = [
        ExportColumn("id", "ID"),
        ExportColumn("question", "问题"),
        ExportColumn("reference_answer", "参考答案"),
        ExportColumn("key_points", "关键点"),
        ExportColumn("negative_points", "负向点"),
        ExportColumn("tags", "标签"),
        ExportColumn("difficulty", "难度"),
        ExportColumn("extra_fields", "扩展字段"),
        ExportColumn("source", "来源"),
        ExportColumn("status", "状态"),
        ExportColumn("category_id", "分类 ID"),
        ExportColumn("created_at", "创建时间"),
        ExportColumn("updated_at", "更新时间"),
    ]
    return build_export_response(rows, columns, format, f"benchmark_{project_id[:8]}_cases")


@router.post("/{project_id}/cases")
async def create_benchmark_case(project_id: str, req: BenchmarkCaseCreate):
    async with async_session_factory() as session:
        row = BenchmarkCaseRow(
            project_id=project_id,
            category_id=req.category_id,
            question=req.question,
            reference_answer=req.reference_answer,
            key_points=req.key_points,
            negative_points=req.negative_points,
            tags=req.tags,
            difficulty=req.difficulty,
            source="manual",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return {"id": str(row.id)}


@router.put("/cases/{case_id}")
async def update_benchmark_case(case_id: str, req: BenchmarkCaseUpdate):
    async with async_session_factory() as session:
        result = await session.execute(select(BenchmarkCaseRow).where(BenchmarkCaseRow.id == case_id))
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Case not found")

        if req.question is not None:
            row.question = req.question
        if req.reference_answer is not None:
            row.reference_answer = req.reference_answer
        if req.key_points is not None:
            row.key_points = req.key_points
        if req.negative_points is not None:
            row.negative_points = req.negative_points
        if req.tags is not None:
            row.tags = req.tags
        if req.difficulty is not None:
            row.difficulty = req.difficulty
        if req.category_id is not None:
            row.category_id = req.category_id
        if req.status is not None:
            row.status = req.status
        if req.extra_fields is not None:
            row.extra_fields = req.extra_fields
        row.updated_at = datetime.now(timezone.utc)

        await session.commit()
    return {"updated": case_id}


@router.delete("/cases/{case_id}")
async def delete_benchmark_case(case_id: str):
    async with async_session_factory() as session:
        result = await session.execute(select(BenchmarkCaseRow).where(BenchmarkCaseRow.id == case_id))
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Case not found")
        await session.delete(row)
        await session.commit()
    return {"deleted": case_id}


@router.post("/{project_id}/import")
async def import_file(
    project_id: str,
    file: UploadFile = File(...),
    category_id: str | None = Query(None),
):
    """Import benchmark cases from CSV, JSON, or XLSX file.

    If the target category has a schema_config, fields are mapped according to
    the schema rules. Extra fields beyond question/reference_answer are stored
    in the extra_fields JSONB column.

    When a category_id is specified, all records go directly into benchmark_cases
    (reference_answer may be null, to be filled later). Only when no category is
    specified AND no answer is found does the record go to candidate_cases.
    """
    content = await file.read()
    filename = file.filename or "unknown"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    try:
        source_headers, rows = parse_upload_file(content, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not rows:
        raise HTTPException(status_code=400, detail="File is empty")

    # Load category schema_config if available
    schema_columns: list[dict] = []
    field_mapping: dict[str, str] = {}

    async with async_session_factory() as session:
        if category_id:
            cat_result = await session.execute(
                select(CategoryRow).where(CategoryRow.id == category_id)
            )
            category = cat_result.scalar_one_or_none()
            if category and category.schema_config:
                schema_columns = category.schema_config.get("columns", [])
                field_mapping = auto_match_columns(source_headers, schema_columns)

        imported = 0
        pending = 0

        for row_data in rows:
            question = get_question_from_row(row_data, schema_columns, field_mapping)
            if not question:
                continue

            ref_answer = get_answer_from_row(row_data, schema_columns, field_mapping)

            # Resolve extra fields from schema
            extra_fields = None
            if schema_columns:
                extra_fields = resolve_extra_fields(
                    row_data, schema_columns, field_mapping, filename
                )

            # Parse standard list fields
            key_points = _parse_list_field(row_data.get("key_points") or row_data.get("关键点", ""))
            negative_points = _parse_list_field(row_data.get("negative_points") or row_data.get("反向关键点", ""))
            tags = _parse_list_field(row_data.get("tags") or row_data.get("标签", ""))
            difficulty = row_data.get("difficulty") or row_data.get("难度")

            # 指定了 category 时直接入库 benchmark_cases（答案可为空，后续补充）
            # 未指定 category 且无答案时进入暂存区
            if category_id or ref_answer:
                case_row = BenchmarkCaseRow(
                    project_id=project_id,
                    category_id=category_id,
                    question=question,
                    reference_answer=ref_answer,
                    key_points=key_points,
                    negative_points=negative_points,
                    tags=tags,
                    difficulty=difficulty,
                    extra_fields=extra_fields,
                    source="file_imported",
                )
                session.add(case_row)
                imported += 1
            else:
                candidate_row = CandidateCaseRow(
                    project_id=project_id,
                    source="file_imported",
                    question=question,
                    tags=tags,
                    extra_metadata=extra_fields,
                    status="pending",
                )
                session.add(candidate_row)
                pending += 1

        batch_row = ImportBatchRow(
            project_id=project_id,
            file_name=filename,
            file_type=ext,
            total_count=imported + pending,
            imported_count=imported,
            pending_count=pending,
        )
        session.add(batch_row)
        await session.commit()

    return {
        "file": filename,
        "total": imported + pending,
        "imported_to_benchmark": imported,
        "pending_in_staging": pending,
        "field_mapping": field_mapping if schema_columns else None,
    }


@router.get("/{project_id}/export")
async def export_benchmark(project_id: str, category_id: str | None = Query(None)):
    async with async_session_factory() as session:
        stmt = select(BenchmarkCaseRow).where(
            BenchmarkCaseRow.project_id == project_id,
            BenchmarkCaseRow.status == "active",
        )
        if category_id:
            stmt = stmt.where(BenchmarkCaseRow.category_id == category_id)
        result = await session.execute(stmt.order_by(BenchmarkCaseRow.created_at))
        cases = result.scalars().all()

    return [_case_to_dict(c) for c in cases]


@router.get("/{project_id}/versions")
async def list_versions(project_id: str):
    async with async_session_factory() as session:
        result = await session.execute(
            select(BenchmarkVersionRow)
            .where(BenchmarkVersionRow.project_id == project_id)
            .order_by(BenchmarkVersionRow.created_at.desc())
        )
        versions = result.scalars().all()
    return [
        {"id": str(v.id), "version_tag": v.version_tag, "description": v.description,
         "case_count": v.case_count, "created_at": v.created_at}
        for v in versions
    ]


@router.post("/{project_id}/versions")
async def create_version(project_id: str, req: CreateVersionRequest):
    async with async_session_factory() as session:
        count_result = await session.execute(
            select(func.count()).where(
                BenchmarkCaseRow.project_id == project_id,
                BenchmarkCaseRow.status == "active",
            )
        )
        case_count = count_result.scalar() or 0

        version_row = BenchmarkVersionRow(
            project_id=project_id,
            version_tag=req.version_tag,
            description=req.description,
            case_count=case_count,
        )
        session.add(version_row)

        await session.execute(
            select(BenchmarkCaseRow).where(
                BenchmarkCaseRow.project_id == project_id,
                BenchmarkCaseRow.status == "active",
                BenchmarkCaseRow.version_id.is_(None),
            )
        )

        await session.commit()
        await session.refresh(version_row)

    return {"id": str(version_row.id), "version_tag": req.version_tag, "case_count": case_count}


def _case_to_dict(c: BenchmarkCaseRow) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "project_id": str(c.project_id),
        "category_id": str(c.category_id) if c.category_id else None,
        "question": c.question,
        "reference_answer": c.reference_answer,
        "key_points": c.key_points,
        "negative_points": c.negative_points,
        "tags": c.tags or [],
        "difficulty": c.difficulty,
        "extra_fields": c.extra_fields,
        "source": c.source,
        "status": c.status,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


@router.get("/categories/{category_id}/schema")
async def get_category_schema(category_id: str):
    """Get the schema_config for a category (subset)."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(CategoryRow).where(CategoryRow.id == category_id)
        )
        category = result.scalar_one_or_none()
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")
    return {
        "id": str(category.id),
        "name": category.name,
        "schema_config": category.schema_config,
    }


@router.put("/categories/{category_id}/schema")
async def update_category_schema(category_id: str, req: SchemaConfigUpdate):
    """Update the schema_config for a category (subset).

    The schema_config defines how imported files are mapped to benchmark fields.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(CategoryRow).where(CategoryRow.id == category_id)
        )
        category = result.scalar_one_or_none()
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")

        category.schema_config = req.schema_config
        await session.commit()

    return {"updated": category_id}


@router.post("/{project_id}/import/preview")
async def preview_import(
    project_id: str,
    file: UploadFile = File(...),
    category_id: str | None = Query(None),
    max_rows: int = Query(5, ge=1, le=20),
):
    """Preview how a file would be imported with the category's schema mapping.

    Returns the auto-detected field mapping and a sample of converted rows.
    """
    content = await file.read()
    filename = file.filename or "unknown"

    try:
        source_headers, rows = parse_upload_file(content, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    schema_columns: list[dict] = []
    field_mapping: dict[str, str] = {}

    if category_id:
        async with async_session_factory() as session:
            cat_result = await session.execute(
                select(CategoryRow).where(CategoryRow.id == category_id)
            )
            category = cat_result.scalar_one_or_none()
            if category and category.schema_config:
                schema_columns = category.schema_config.get("columns", [])
                field_mapping = auto_match_columns(source_headers, schema_columns)

    preview_rows = []
    for row_data in rows[:max_rows]:
        question = get_question_from_row(row_data, schema_columns, field_mapping)
        answer = get_answer_from_row(row_data, schema_columns, field_mapping)
        extra = resolve_extra_fields(row_data, schema_columns, field_mapping, filename) if schema_columns else None

        preview_rows.append({
            "question": question,
            "reference_answer": answer,
            "extra_fields": extra,
            "has_answer": bool(answer),
        })

    return {
        "file": filename,
        "total_rows": len(rows),
        "source_headers": source_headers,
        "field_mapping": field_mapping,
        "schema_columns": schema_columns,
        "preview": preview_rows,
    }


def _parse_list_field(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str):
        if value.startswith("["):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        return [v.strip() for v in value.split(",") if v.strip()]
    return []
