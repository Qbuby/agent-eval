"""HTTP API for the Langfuse-backed evaluation workbench."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from agent_eval.api.schemas import (
    BuiltinEvaluator,
    EvalRunDetail,
    EvalRunSummary,
    EvalResultRow,
    EvalResultsPage,
    StartEvalRequest,
)
from agent_eval.db import async_session_factory
from agent_eval.db_models.repository import Repository
from agent_eval.db_models.tables import (
    BenchmarkCaseRow,
    EvaluationScoreRow,
    TestResultRow,
)
from agent_eval.evaluation.langfuse_runner import (
    BUILTIN_EVALUATORS,
    get_run_progress,
    request_stop,
    start_run,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/eval", tags=["eval"])


@router.get("/evaluators/builtin", response_model=list[BuiltinEvaluator])
async def list_builtin_evaluators():
    return [
        BuiltinEvaluator(
            name=name,
            description=spec["description"],
            params_schema=spec["params_schema"],
        )
        for name, spec in BUILTIN_EVALUATORS.items()
    ]


@router.post("/runs/start")
async def start_eval(req: StartEvalRequest):
    if not req.benchmark_version_id and not req.project_id:
        raise HTTPException(status_code=400, detail="benchmark_version_id or project_id is required")
    # ── 1. resolve cases from the benchmark scope ──
    async with async_session_factory() as session:
        stmt = select(BenchmarkCaseRow)
        if req.benchmark_version_id:
            stmt = stmt.where(BenchmarkCaseRow.version_id == uuid.UUID(req.benchmark_version_id))
        else:
            stmt = stmt.where(BenchmarkCaseRow.project_id == uuid.UUID(req.project_id))
        if req.case_ids:
            ids = [uuid.UUID(x) for x in req.case_ids]
            stmt = stmt.where(BenchmarkCaseRow.id.in_(ids))
        if req.filter_category_id:
            stmt = stmt.where(BenchmarkCaseRow.category_id == uuid.UUID(req.filter_category_id))
        if req.filter_tags:
            # postgres array overlap
            stmt = stmt.where(BenchmarkCaseRow.tags.overlap(req.filter_tags))
        if req.limit:
            stmt = stmt.limit(req.limit)
        cases = (await session.execute(stmt)).scalars().all()

    if not cases:
        raise HTTPException(status_code=400, detail="no cases match the selection")

    agent_cfg = req.agent.model_dump()
    evaluator_cfgs = [e.model_dump() for e in req.evaluators]
    if not evaluator_cfgs:
        raise HTTPException(status_code=400, detail="at least one evaluator required")
    for ev in evaluator_cfgs:
        if ev["name"] not in BUILTIN_EVALUATORS:
            raise HTTPException(status_code=400, detail=f"unknown evaluator: {ev['name']}")

    try:
        run_id = await start_run(
            benchmark_version_id=req.benchmark_version_id,
            project_id=req.project_id,
            cases=list(cases),
            agent_cfg=agent_cfg,
            evaluator_cfgs=evaluator_cfgs,
            concurrency=req.concurrency,
            run_name=req.run_name,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to start run: {e}") from e
    return {"run_id": run_id, "status": "running", "case_count": len(cases)}


def _row_to_summary(row: Any, progress: dict[str, int] | None = None) -> EvalRunSummary:
    return EvalRunSummary(
        id=str(row.id),
        benchmark_version_id=str(row.benchmark_version_id) if row.benchmark_version_id else None,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        langfuse_run_name=row.langfuse_run_name,
        agent_config=row.agent_config or {},
        summary_scores=row.summary_scores,
        progress=progress or {},
        created_at=row.created_at,
    )


@router.get("/runs")
async def list_runs(
    benchmark_version_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    bv_uuid = uuid.UUID(benchmark_version_id) if benchmark_version_id else None
    async with async_session_factory() as session:
        repo = Repository(session)
        rows, total = await repo.list_test_runs(
            benchmark_version_id=bv_uuid, status=status,
            page=page, page_size=page_size,
        )
    items = []
    for r in rows:
        prog = get_run_progress(str(r.id)) if r.status == "running" else {}
        items.append(_row_to_summary(r, prog))
    return {"items": [it.model_dump(mode="json") for it in items], "total": total, "page": page, "page_size": page_size}


@router.get("/runs/{run_id}", response_model=EvalRunDetail)
async def get_run(run_id: str):
    run_uuid = uuid.UUID(run_id)
    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.get_test_run(run_uuid)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    prog = get_run_progress(run_id) if row.status == "running" else {}
    base = _row_to_summary(row, prog)
    return EvalRunDetail(
        **base.model_dump(),
        evaluator_configs=row.evaluator_configs or [],
    )


@router.get("/runs/{run_id}/results", response_model=EvalResultsPage)
async def get_run_results(
    run_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    run_uuid = uuid.UUID(run_id)
    async with async_session_factory() as session:
        repo = Repository(session)
        rows, total = await repo.get_results_paginated(run_uuid, page=page, page_size=page_size)
        # Pull scores for these rows
        result_ids = [r.id for r in rows]
        score_rows = []
        if result_ids:
            score_rows = (await session.execute(
                select(EvaluationScoreRow).where(EvaluationScoreRow.result_id.in_(result_ids))
            )).scalars().all()
    score_index: dict[uuid.UUID, dict[str, float]] = {}
    for s in score_rows:
        score_index.setdefault(s.result_id, {})[s.dimension] = float(s.score)

    items: list[EvalResultRow] = []
    for r in rows:
        items.append(EvalResultRow(
            id=str(r.id),
            benchmark_case_id=str(r.benchmark_case_id) if r.benchmark_case_id else None,
            test_case_id=str(r.test_case_id) if r.test_case_id else None,
            status=r.status,
            actual_output=r.actual_output,
            latency_ms=r.latency_ms,
            total_tokens=r.total_tokens,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            tool_call_count=r.tool_call_count,
            error_message=r.error_message,
            langfuse_trace_id=r.langfuse_trace_id,
            scores=score_index.get(r.id, {}),
        ))
    return EvalResultsPage(items=items, total=total, page=page, page_size=page_size)


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str):
    ok = request_stop(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="run not found or already finished")
    return {"run_id": run_id, "status": "stopping"}
