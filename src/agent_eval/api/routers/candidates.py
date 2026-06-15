from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select

from agent_eval.api.exporters import ExportColumn, build_export_response, validate_format
from agent_eval.auth.dependencies import (
    ROLE_ADMIN,
    require_internal,
    require_role,
)
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import BenchmarkCaseRow, CandidateCaseRow, CategoryRow
from agent_eval.data.benchmark_import import (
    auto_detect_field_mapping,
    collect_sample_values,
    iter_upload_rows,
    parse_upload_file,
    resolve_question_answer,
)

# Router-level login gate: every endpoint requires an authenticated user.
# Preserves the auth.enabled bypass; destructive candidate deletion requires admin.
router = APIRouter(
    prefix="/api/candidates",
    tags=["candidates"],
    dependencies=[Depends(require_internal())],
)


class CandidateCreate(BaseModel):
    project_id: str | None = None
    dataset_name: str | None = None
    question: str
    answer: str | None = None
    key_points: list[str] | None = None
    negative_points: list[str] | None = None
    tags: list[str] = []
    category: str | None = None  # 自由文本类别名
    source: str = "manual"


class CandidateUpdate(BaseModel):
    question: str | None = None
    answer: str | None = None
    key_points: list[str] | None = None
    negative_points: list[str] | None = None
    tags: list[str] | None = None
    category: str | None = None
    project_id: str | None = None
    status: str | None = None


class BatchReviewRequest(BaseModel):
    ids: list[str]
    action: str  # approve | reject


class PromoteRequest(BaseModel):
    ids: list[str]
    project_id: str
    category_id: str | None = None


@router.get("")
async def list_candidates(
    status: str | None = Query(None, description="Filter: pending, ready, imported, rejected"),
    project_id: str | None = Query(None),
    dataset_name: str | None = Query(None),
    source: str | None = Query(None),
    category: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    async with async_session_factory() as session:
        stmt = select(CandidateCaseRow)
        if status:
            stmt = stmt.where(CandidateCaseRow.status == status)
        if project_id:
            stmt = stmt.where(CandidateCaseRow.project_id == project_id)
        if dataset_name:
            stmt = stmt.where(CandidateCaseRow.dataset_name == dataset_name)
        if source:
            stmt = stmt.where(CandidateCaseRow.source == source)
        if category:
            stmt = stmt.where(CandidateCaseRow.category == category)
        if search:
            stmt = stmt.where(CandidateCaseRow.question.ilike(f"%{search}%"))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(CandidateCaseRow.created_at.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await session.execute(stmt)
        cases = result.scalars().all()

    return {
        "items": [_candidate_to_dict(c) for c in cases],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/categories")
async def list_candidate_categories(
    dataset_name: str | None = Query(None, description="按数据集过滤"),
    project_id: str | None = Query(None),
):
    """聚合备选样例里出现过的自由文本类别名（去重、非空），供前端下拉/管理。"""
    async with async_session_factory() as session:
        stmt = select(CandidateCaseRow.category).where(
            CandidateCaseRow.category.isnot(None)
        )
        if dataset_name:
            stmt = stmt.where(CandidateCaseRow.dataset_name == dataset_name)
        if project_id:
            stmt = stmt.where(CandidateCaseRow.project_id == project_id)
        stmt = stmt.distinct().order_by(CandidateCaseRow.category)
        result = await session.execute(stmt)
        names = [c for c in result.scalars().all() if c and c.strip()]
    return {"categories": names}


@router.get("/export")
async def export_candidates(
    status: str | None = Query(None),
    project_id: str | None = Query(None),
    dataset_name: str | None = Query(None),
    source: str | None = Query(None),
    search: str | None = Query(None),
    format: str = Query("csv"),
):
    """Export all candidate cases matching the current filters (no pagination)."""
    validate_format(format)
    async with async_session_factory() as session:
        stmt = select(CandidateCaseRow)
        if status:
            stmt = stmt.where(CandidateCaseRow.status == status)
        if project_id:
            stmt = stmt.where(CandidateCaseRow.project_id == project_id)
        if dataset_name:
            stmt = stmt.where(CandidateCaseRow.dataset_name == dataset_name)
        if source:
            stmt = stmt.where(CandidateCaseRow.source == source)
        if search:
            stmt = stmt.where(CandidateCaseRow.question.ilike(f"%{search}%"))
        stmt = stmt.order_by(CandidateCaseRow.created_at.desc())
        result = await session.execute(stmt)
        cases = result.scalars().all()

    rows = [_candidate_to_dict(c) for c in cases]
    columns = [
        ExportColumn("id", "ID"),
        ExportColumn("question", "问题"),
        ExportColumn("answer", "答案"),
        ExportColumn("key_points", "关键点"),
        ExportColumn("negative_points", "负向点"),
        ExportColumn("tags", "标签"),
        ExportColumn("source", "来源"),
        ExportColumn("status", "状态"),
        ExportColumn("project_id", "项目 ID"),
        ExportColumn("langsmith_example_id", "LangSmith 样例 ID"),
        ExportColumn("reviewed_at", "审核时间"),
        ExportColumn("created_at", "创建时间"),
        ExportColumn("updated_at", "更新时间"),
    ]
    return build_export_response(rows, columns, format, "candidates")


@router.post("")
async def create_candidate(req: CandidateCreate):
    has_answer = bool(req.answer and req.answer.strip())
    status = "ready" if has_answer else "pending"

    async with async_session_factory() as session:
        row = CandidateCaseRow(
            project_id=req.project_id,
            dataset_name=req.dataset_name,
            category=req.category,
            source=req.source,
            question=req.question,
            answer=req.answer,
            key_points=req.key_points,
            negative_points=req.negative_points,
            tags=req.tags,
            status=status,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return {"id": str(row.id), "status": status}


@router.put("/{case_id}")
async def update_candidate(case_id: str, req: CandidateUpdate):
    async with async_session_factory() as session:
        result = await session.execute(select(CandidateCaseRow).where(CandidateCaseRow.id == case_id))
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Candidate not found")

        if req.question is not None:
            row.question = req.question
        if req.answer is not None:
            row.answer = req.answer
        if req.key_points is not None:
            row.key_points = req.key_points
        if req.negative_points is not None:
            row.negative_points = req.negative_points
        if req.tags is not None:
            row.tags = req.tags
        if req.category is not None:
            row.category = req.category or None
        if req.project_id is not None:
            row.project_id = req.project_id
        if req.status is not None:
            row.status = req.status

        if row.answer and row.answer.strip() and row.status == "pending":
            row.status = "ready"

        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
    return {"updated": case_id, "status": row.status}


@router.delete("/{case_id}", dependencies=[Depends(require_role(ROLE_ADMIN))])
async def delete_candidate(case_id: str):
    async with async_session_factory() as session:
        result = await session.execute(select(CandidateCaseRow).where(CandidateCaseRow.id == case_id))
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Candidate not found")
        await session.delete(row)
        await session.commit()
    return {"deleted": case_id}


@router.post("/batch-review")
async def batch_review(req: BatchReviewRequest):
    if req.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")

    new_status = "ready" if req.action == "approve" else "rejected"

    async with async_session_factory() as session:
        for cid in req.ids:
            result = await session.execute(select(CandidateCaseRow).where(CandidateCaseRow.id == cid))
            row = result.scalar_one_or_none()
            if row:
                row.status = new_status
                row.reviewed_at = datetime.now(timezone.utc)
        await session.commit()

    return {"reviewed": len(req.ids), "new_status": new_status}


@router.post("/promote")
async def promote_to_benchmark(req: PromoteRequest):
    """Promote ready candidates to the benchmark test set.

    类别同步（有则入无则增）：candidate 的自由文本 ``category`` 在目标 project
    下按名匹配 CategoryRow——已存在则复用其 id，不存在则新建一个同名 category。
    解析出的 category_id 落到 benchmark_row。candidate 无 category 时回退到请求
    显式传入的 ``req.category_id``（旧行为，作为兜底默认）。
    """
    promoted = 0
    created_categories = 0
    # 按名缓存目标 project 下的 category_id，避免同名重复查询/创建。
    name_to_cat_id: dict[str, str] = {}

    async def resolve_category_id(cat_name: str | None) -> str | None:
        """目标 project 下按名取或建 CategoryRow，返回 id。无名则回退 req.category_id。"""
        name = (cat_name or "").strip()
        if not name:
            return req.category_id
        if name in name_to_cat_id:
            return name_to_cat_id[name]
        existing = await session.execute(
            select(CategoryRow).where(
                CategoryRow.project_id == req.project_id,
                CategoryRow.name == name,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            row = CategoryRow(project_id=req.project_id, name=name)
            session.add(row)
            await session.flush()  # 取 row.id
            nonlocal created_categories
            created_categories += 1
        cid = str(row.id)
        name_to_cat_id[name] = cid
        return cid

    async with async_session_factory() as session:
        for cid in req.ids:
            result = await session.execute(select(CandidateCaseRow).where(CandidateCaseRow.id == cid))
            candidate = result.scalar_one_or_none()
            if not candidate:
                continue
            if candidate.status not in ("ready",):
                continue

            category_id = await resolve_category_id(candidate.category)

            benchmark_row = BenchmarkCaseRow(
                project_id=req.project_id,
                category_id=category_id,
                question=candidate.question,
                reference_answer=candidate.answer,
                key_points=candidate.key_points or [],
                negative_points=candidate.negative_points or [],
                tags=candidate.tags or [],
                source=f"candidate_{candidate.source}",
                source_case_id=candidate.id,
            )
            session.add(benchmark_row)

            candidate.status = "imported"
            candidate.reviewed_at = datetime.now(timezone.utc)
            promoted += 1

        await session.commit()

    return {
        "promoted": promoted,
        "project_id": req.project_id,
        "categories_created": created_categories,
    }


def _candidate_to_dict(c: CandidateCaseRow) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "project_id": str(c.project_id) if c.project_id else None,
        "category": c.category,
        "source": c.source,
        "question": c.question,
        "answer": c.answer,
        "key_points": c.key_points,
        "negative_points": c.negative_points,
        "tags": c.tags or [],
        "langsmith_example_id": c.langsmith_example_id,
        "status": c.status,
        "reviewed_at": c.reviewed_at,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


class ImportFromLangSmithRequest(BaseModel):
    dataset_name: str
    project_id: str | None = None
    limit: int | None = None


class ImportFromTracesRequest(BaseModel):
    project_name: str
    run_ids: list[str]
    target_project_id: str | None = None
    dataset_name: str | None = None


@router.post("/import-langsmith")
async def import_from_langsmith(req: ImportFromLangSmithRequest):
    """Import examples from a LangSmith dataset into candidate_cases."""
    from agent_eval.api.dependencies import get_manager

    mgr = await get_manager()
    try:
        cases = await mgr.load_cases(req.dataset_name, limit=req.limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LangSmith error: {e}") from e

    imported = 0
    async with async_session_factory() as session:
        for case in cases:
            question = ""
            if case.input_messages:
                for msg in reversed(case.input_messages):
                    if msg.get("role") == "user":
                        question = msg.get("content", "")
                        break
            if not question:
                continue

            row = CandidateCaseRow(
                project_id=req.project_id,
                dataset_name=req.dataset_name,
                source="trace_imported",
                question=question,
                answer=case.expected_output,
                key_points=case.expected_output_criteria or None,
                tags=case.tags or [],
                langsmith_example_id=case.id,
                status="ready" if case.expected_output else "pending",
            )
            session.add(row)
            imported += 1
        await session.commit()

    return {"imported": imported, "dataset": req.dataset_name}


@router.post("/import-traces")
async def import_from_traces(req: ImportFromTracesRequest):
    """Import runs from LangSmith traces into candidate_cases (question only, no answer)."""
    from agent_eval.api.dependencies import get_extractor

    ext = await get_extractor()
    try:
        cases = await ext.extract_test_cases_fast(
            req.project_name, req.run_ids,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LangSmith error: {e}") from e

    imported = 0
    async with async_session_factory() as session:
        for case in cases:
            question = ""
            if case.input_messages:
                for msg in reversed(case.input_messages):
                    if msg.get("role") == "user":
                        question = msg.get("content", "")
                        break
            if not question:
                continue

            row = CandidateCaseRow(
                project_id=req.target_project_id,
                dataset_name=req.dataset_name or req.project_name,
                source="trace_imported",
                question=question,
                tags=case.tags or [],
                status="pending",
            )
            session.add(row)
            imported += 1
        await session.commit()

    return {"imported": imported}


def _parse_list_field(value: Any) -> list[str]:
    """把单元格值解析成字符串列表：JSON 数组、逗号分隔或已是 list 都支持。"""
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


@router.post("/import/preview")
async def preview_candidate_import(
    file: UploadFile = File(...),
    max_rows: int = Query(5, ge=1, le=50),
    question_column: str | None = Query(None),
    answer_column: str | None = Query(None),
):
    """预览备选数据集文件导入：识别列 + 自动建议问题/答案列 + 样例行。

    与基准导入的 preview 一比一对齐，但备选无 category/schema 概念，故只做
    schema 无关的列自动识别（auto_detect_field_mapping）。
    """
    content = await file.read()
    filename = file.filename or "unknown"

    try:
        source_headers, rows = parse_upload_file(content, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # schema 无关的问题 / 答案列自动识别。
    suggested_mapping = auto_detect_field_mapping(source_headers)
    sample_values = collect_sample_values(rows, source_headers, limit=3)

    # 预览行：显式覆盖优先，否则回退自动识别列，避免非标准列名时样例空白。
    eff_question_col = question_column or suggested_mapping.get("question")
    eff_answer_col = answer_column or suggested_mapping.get("reference_answer")

    preview_rows = []
    for row_data in rows[:max_rows]:
        question, answer = resolve_question_answer(
            row_data,
            question_column=eff_question_col,
            answer_column=eff_answer_col,
            schema_columns=[],
            field_mapping={},
        )
        preview_rows.append({
            "question": question,
            "reference_answer": answer,
            "extra_fields": None,
            "has_answer": bool(answer),
        })

    return {
        "file": filename,
        "total_rows": len(rows),
        "source_headers": source_headers,
        "field_mapping": {},
        "suggested_mapping": suggested_mapping,
        "sample_values": sample_values,
        "schema_columns": [],
        "preview": preview_rows,
    }


@router.post("/import")
async def import_candidate_file(
    file: UploadFile = File(...),
    project_id: str | None = Query(None),
    dataset_name: str | None = Query(None, description="归属数据集名"),
    category: str | None = Query(None, description="自由文本类别名，落到本批所有样例"),
    question_column: str | None = Query(None, description="手动指定问题列（覆盖自动识别）"),
    answer_column: str | None = Query(None, description="手动指定期望答案列（覆盖自动识别）"),
):
    """从 CSV / JSON(L) / XLSX 文件导入备选样例到 candidate_cases。

    流式逐行读取并分批提交，避免大文件 OOM。字段识别优先级：显式
    question_column/answer_column（来自 UI） > 别名/硬编码兜底。备选无
    schema 概念，故不做 schema 映射、不记 import_batches。有答案的样例
    状态为 ready，无答案为 pending（暂存区）。``category`` 为自由文本类别名，
    本批所有样例统一归入；promote 到基准时按名同步 CategoryRow（有则入无则增）。
    """
    category = (category or "").strip() or None
    content = await file.read()
    filename = file.filename or "unknown"

    try:
        source_headers, row_iter = iter_upload_rows(content, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    BATCH_SIZE = 500
    ready = 0
    pending = 0
    skipped = 0
    duplicates = 0
    pending_batch: list[Any] = []

    async with async_session_factory() as session:
        # 去重：按问题文本，预加载同 dataset（无则同 project）已有的候选问题到
        # set，每行 O(1) 检查；seen 也兜住文件内自身重复。
        dedup_stmt = select(CandidateCaseRow.question)
        if dataset_name:
            dedup_stmt = dedup_stmt.where(CandidateCaseRow.dataset_name == dataset_name)
        elif project_id:
            dedup_stmt = dedup_stmt.where(CandidateCaseRow.project_id == project_id)
        existing = await session.execute(dedup_stmt)
        seen: set[str] = {
            (q or "").strip() for q in existing.scalars().all() if q and q.strip()
        }

        async def flush_batch() -> None:
            if pending_batch:
                session.add_all(pending_batch)
                await session.flush()
                pending_batch.clear()

        for row_data in row_iter:
            question, answer = resolve_question_answer(
                row_data,
                question_column=question_column,
                answer_column=answer_column,
                schema_columns=[],
                field_mapping={},
            )
            if not question:
                skipped += 1
                continue

            if question in seen:
                duplicates += 1
                continue
            seen.add(question)

            key_points = _parse_list_field(row_data.get("key_points") or row_data.get("关键点", ""))
            negative_points = _parse_list_field(row_data.get("negative_points") or row_data.get("反向关键点", ""))
            tags = _parse_list_field(row_data.get("tags") or row_data.get("标签", ""))

            # 类别：行内「category/类别」列优先，否则用端点统一 category 兜底。
            row_cat = row_data.get("category") or row_data.get("类别") or row_data.get("分类")
            row_category = (str(row_cat).strip() if row_cat else None) or category

            has_answer = bool(answer and answer.strip())
            pending_batch.append(CandidateCaseRow(
                project_id=project_id,
                dataset_name=dataset_name,
                category=row_category,
                source="file_imported",
                question=question,
                answer=answer or None,
                key_points=key_points or None,
                negative_points=negative_points or None,
                tags=tags,
                status="ready" if has_answer else "pending",
            ))
            if has_answer:
                ready += 1
            else:
                pending += 1

            if len(pending_batch) >= BATCH_SIZE:
                await flush_batch()

        await flush_batch()

        # 仅当什么都没识别到才报错；全是重复是正常的重复上传，返回 200。
        if ready + pending + duplicates == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No importable rows (file empty or no question column "
                    f"matched; skipped {skipped})"
                ),
            )

        await session.commit()

    used_mapping: dict[str, str] = {}
    if question_column:
        used_mapping["question"] = question_column
    if answer_column:
        used_mapping["reference_answer"] = answer_column

    return {
        "file": filename,
        "total": ready + pending,
        "imported_to_benchmark": ready,
        "pending_in_staging": pending,
        "skipped": skipped,
        "duplicates": duplicates,
        "field_mapping": used_mapping or None,
    }
