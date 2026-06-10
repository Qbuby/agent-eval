from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from agent_eval.api.exporters import ExportColumn, build_export_response, validate_format
from agent_eval.auth.dependencies import (
    ROLE_ADMIN,
    get_current_user,
    require_role,
)
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import BenchmarkCaseRow, CandidateCaseRow

# Router-level login gate: every endpoint requires an authenticated user.
# Preserves the auth.enabled bypass; destructive candidate deletion requires admin.
router = APIRouter(
    prefix="/api/candidates",
    tags=["candidates"],
    dependencies=[Depends(get_current_user)],
)


class CandidateCreate(BaseModel):
    project_id: str | None = None
    dataset_name: str | None = None
    question: str
    answer: str | None = None
    key_points: list[str] | None = None
    negative_points: list[str] | None = None
    tags: list[str] = []
    source: str = "manual"


class CandidateUpdate(BaseModel):
    question: str | None = None
    answer: str | None = None
    key_points: list[str] | None = None
    negative_points: list[str] | None = None
    tags: list[str] | None = None
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
    """Promote ready candidates to the benchmark test set."""
    promoted = 0

    async with async_session_factory() as session:
        for cid in req.ids:
            result = await session.execute(select(CandidateCaseRow).where(CandidateCaseRow.id == cid))
            candidate = result.scalar_one_or_none()
            if not candidate:
                continue
            if candidate.status not in ("ready",):
                continue

            benchmark_row = BenchmarkCaseRow(
                project_id=req.project_id,
                category_id=req.category_id,
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

    return {"promoted": promoted, "project_id": req.project_id}


def _candidate_to_dict(c: CandidateCaseRow) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "project_id": str(c.project_id) if c.project_id else None,
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
