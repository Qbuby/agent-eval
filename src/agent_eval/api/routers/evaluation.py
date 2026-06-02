"""HTTP API for the evaluation workbench."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from agent_eval.api.dependencies import get_extractor
from agent_eval.api.exporters import ExportColumn, build_export_response, validate_format
from agent_eval.api.schemas import (
    BuiltinEvaluator,
    CreateEvaluatorRequest,
    CreateEvaluatorVersionRequest,
    DryRunRequest,
    DryRunResponse,
    DryRunScoreItem,
    EvalCaseSourceSummary,
    EvalRunDetail,
    EvalRunSummary,
    EvalResultRow,
    EvalResultsPage,
    EvaluatorInstance,
    EvaluatorVersion,
    RunDetailResponse,
    StartEvalRequest,
    UpdateEvaluatorRequest,
    UploadCasesResponse,
)
from agent_eval.data.trace_extractor import TraceExtractor
from agent_eval.db import async_session_factory
from agent_eval.db_models.repository import Repository
from agent_eval.db_models.tables import (
    BenchmarkCaseRow,
    EvaluationScoreRow,
    TestResultRow,
    TestRunRow,
)
from agent_eval.evaluation.langfuse_runner import (
    BUILTIN_EVALUATORS,
    _aggregate_cost,
    get_run_progress,
    request_stop,
    rerun_backfill,
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
    # ── 1. resolve cases ──────────────────────────────────────────
    sources = [x for x in (req.benchmark_version_id, req.project_id, req.case_source_id) if x]
    if not sources:
        raise HTTPException(
            status_code=400,
            detail="one of benchmark_version_id / project_id / case_source_id is required",
        )
    if len(sources) > 1:
        raise HTTPException(
            status_code=400,
            detail="provide only one source: benchmark_version_id OR project_id OR case_source_id",
        )

    cases: list[dict[str, Any]] = []

    async with async_session_factory() as session:
        repo = Repository(session)

        if req.case_source_id:
            # ── uploaded file ──
            src = await repo.get_eval_case_source(uuid.UUID(req.case_source_id))
            if src is None:
                raise HTTPException(status_code=404, detail="case_source not found")
            raw_cases = src.cases or []
            if req.limit:
                raw_cases = raw_cases[: req.limit]
            for c in raw_cases:
                cases.append({
                    "id": c.get("name") or f"case-{len(cases)+1}",
                    "name": c.get("name") or f"case-{len(cases)+1}",
                    "question": c.get("question") or "",
                    "expected_output": c.get("expected_output") or "",
                    "expected_tool_calls": [],
                    "metadata": c.get("metadata") or {},
                    "source": "file",
                })
        else:
            # ── benchmark dataset ──
            stmt = select(BenchmarkCaseRow)
            if req.benchmark_version_id:
                stmt = stmt.where(BenchmarkCaseRow.version_id == uuid.UUID(req.benchmark_version_id))
            else:
                stmt = stmt.where(BenchmarkCaseRow.project_id == uuid.UUID(req.project_id))
            if req.case_ids:
                stmt = stmt.where(BenchmarkCaseRow.id.in_([uuid.UUID(x) for x in req.case_ids]))
            if req.filter_category_id:
                stmt = stmt.where(BenchmarkCaseRow.category_id == uuid.UUID(req.filter_category_id))
            if req.filter_tags:
                stmt = stmt.where(BenchmarkCaseRow.tags.overlap(req.filter_tags))
            if req.limit:
                stmt = stmt.limit(req.limit)
            bench_rows = (await session.execute(stmt)).scalars().all()
            for b in bench_rows:
                expected_tool_calls = []
                if isinstance(b.extra_fields, dict):
                    for t in (b.extra_fields.get("expected_tool_calls") or []):
                        nm = t.get("tool_name") or t.get("name") if isinstance(t, dict) else None
                        if nm:
                            expected_tool_calls.append({"tool_name": nm})
                cases.append({
                    "id": str(b.id),
                    "name": str(b.id)[:8],
                    "question": b.question,
                    "expected_output": b.reference_answer or "",
                    "expected_tool_calls": expected_tool_calls,
                    "metadata": {"tags": list(b.tags or [])},
                    "source": "benchmark",
                })

        if not cases:
            raise HTTPException(status_code=400, detail="no cases match the selection")

        # ── 2. resolve evaluator instances ──
        if not req.evaluator_ids:
            raise HTTPException(status_code=400, detail="at least one evaluator_id is required")
        evaluator_specs: list[dict[str, Any]] = []
        for eid in req.evaluator_ids:
            row = await repo.get_evaluator_config(uuid.UUID(eid))
            if row is None:
                raise HTTPException(status_code=404, detail=f"evaluator not found: {eid}")
            if not row.is_active:
                raise HTTPException(status_code=400, detail=f"evaluator inactive: {row.name}")
            evaluator_specs.append({
                "id": str(row.id),
                "label": row.name,
                "tag": row.tag or row.name,
                "evaluator_type": row.evaluator_type,
                "params": row.params or {},
                # Pin to the active version so historical reproductions don't
                # silently follow future edits. Stored verbatim into
                # test_runs.evaluator_configs[].evaluator_version_id.
                "evaluator_version_id": (
                    str(row.current_version_id) if row.current_version_id else None
                ),
            })

    agent_cfg = req.agent.model_dump()

    try:
        run_id = await start_run(
            cases=cases,
            agent_cfg=agent_cfg,
            evaluator_specs=evaluator_specs,
            concurrency=req.concurrency,
            run_name=req.run_name,
            langsmith_project=req.langsmith_project,
            benchmark_version_id=req.benchmark_version_id,
            eval_case_source_id=req.case_source_id,
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
        langsmith_project=row.langsmith_project,
        agent_config=row.agent_config or {},
        summary_scores=row.summary_scores,
        progress=progress or {},
        created_at=row.created_at,
    )


@router.get("/runs")
async def list_runs(
    benchmark_version_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    started_after: str | None = Query(default=None, description="ISO timestamp lower bound (inclusive)"),
    started_before: str | None = Query(default=None, description="ISO timestamp upper bound (inclusive)"),
    q: str | None = Query(default=None, description="search run name / model / url / project"),
    min_pass_rate: float | None = Query(default=None, ge=0.0, le=1.0,
                                        description="filter to runs with pass rate >= this fraction"),
    include_deleted: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    from datetime import datetime as _dt
    bv_uuid = uuid.UUID(benchmark_version_id) if benchmark_version_id else None

    def _parse_ts(s: str | None):
        if not s:
            return None
        try:
            # accept both 'Z' suffix and offset-aware ISO strings
            return _dt.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"invalid ISO timestamp: {s}") from e

    async with async_session_factory() as session:
        repo = Repository(session)
        rows, total = await repo.list_test_runs(
            benchmark_version_id=bv_uuid, status=status,
            started_after=_parse_ts(started_after),
            started_before=_parse_ts(started_before),
            text_query=q,
            min_pass_rate=min_pass_rate,
            include_deleted=include_deleted,
            page=page, page_size=page_size,
        )
    items = []
    for r in rows:
        prog = get_run_progress(str(r.id)) if r.status == "running" else {}
        items.append(_row_to_summary(r, prog))
    return {"items": [it.model_dump(mode="json") for it in items], "total": total, "page": page, "page_size": page_size}


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str):
    """Soft-delete: stamps deleted_at. Row stays in DB so historical
    Langfuse / LangSmith deep-links keep working; default list view hides it."""
    run_uuid = uuid.UUID(run_id)
    async with async_session_factory() as session:
        repo = Repository(session)
        ok = await repo.soft_delete_test_run(run_uuid)
        if not ok:
            raise HTTPException(status_code=404, detail="run not found or already deleted")
        await session.commit()
    return {"run_id": run_id, "deleted": True}


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
            question=r.question,
            latency_ms=r.latency_ms,
            total_tokens=r.total_tokens,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            cache_creation_tokens=r.cache_creation_tokens,
            cache_read_tokens=r.cache_read_tokens,
            tool_call_count=r.tool_call_count,
            first_thinking_token_ms=getattr(r, "first_thinking_token_ms", None),
            first_answer_token_ms=getattr(r, "first_answer_token_ms", None),
            actual_tool_calls=r.actual_tool_calls,
            full_trace=r.full_trace,
            error_message=r.error_message,
            langfuse_trace_id=r.langfuse_trace_id,
            langsmith_run_id=r.langsmith_run_id,
            attempts_made=getattr(r, "attempts_made", 1) or 1,
            scores=score_index.get(r.id, {}),
        ))
    return EvalResultsPage(items=items, total=total, page=page, page_size=page_size)


async def _collect_run_results(run_uuid: uuid.UUID) -> tuple[Any, list[dict[str, Any]], list[str]]:
    """Load a run + all its results (no pagination) with scores merged in.

    Returns (run_row, rows, score_dimensions). ``rows`` are plain dicts ready
    for export; ``score_dimensions`` is the sorted union of every dimension
    seen so the exporter can give each its own column.
    """
    async with async_session_factory() as session:
        repo = Repository(session)
        run_row = await repo.get_test_run(run_uuid)
        if run_row is None:
            raise HTTPException(status_code=404, detail="run not found")
        results = await repo.get_results_by_run(run_uuid)
        scores_by_result = await repo.get_scores_by_run(run_uuid)

    dims: set[str] = set()
    rows: list[dict[str, Any]] = []
    for r in sorted(results, key=lambda x: x.created_at or x.id.hex):
        score_map = {s.dimension: float(s.score) for s in scores_by_result.get(r.id, [])}
        dims.update(score_map.keys())
        rows.append({
            "id": str(r.id),
            "benchmark_case_id": str(r.benchmark_case_id) if r.benchmark_case_id else None,
            "test_case_id": str(r.test_case_id) if r.test_case_id else None,
            "question": r.question,
            "status": r.status,
            "actual_output": r.actual_output,
            "latency_ms": r.latency_ms,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "total_tokens": r.total_tokens,
            "cache_creation_tokens": r.cache_creation_tokens,
            "cache_read_tokens": r.cache_read_tokens,
            "tool_call_count": r.tool_call_count,
            "first_thinking_token_ms": getattr(r, "first_thinking_token_ms", None),
            "first_answer_token_ms": getattr(r, "first_answer_token_ms", None),
            "attempts_made": getattr(r, "attempts_made", 1) or 1,
            "actual_tool_calls": r.actual_tool_calls,
            "error_message": r.error_message,
            "langfuse_trace_id": r.langfuse_trace_id,
            "langsmith_run_id": r.langsmith_run_id,
            "scores": score_map,
            **{f"score::{d}": score_map.get(d) for d in score_map},
        })
    return run_row, rows, sorted(dims)


@router.get("/runs/{run_id}/results/export")
async def export_run_results(run_id: str, format: str = Query("csv")):
    """Export all per-sample results of a run as csv / json / xlsx."""
    validate_format(format)
    run_uuid = uuid.UUID(run_id)
    _run_row, rows, dims = await _collect_run_results(run_uuid)

    columns = [
        ExportColumn("id", "结果 ID"),
        ExportColumn("benchmark_case_id", "基准用例 ID"),
        ExportColumn("question", "问题"),
        ExportColumn("status", "状态"),
        ExportColumn("actual_output", "实际输出"),
        ExportColumn("latency_ms", "时延(ms)"),
        ExportColumn("prompt_tokens", "输入 token"),
        ExportColumn("completion_tokens", "输出 token"),
        ExportColumn("total_tokens", "总 token"),
        ExportColumn("cache_creation_tokens", "缓存写入 token"),
        ExportColumn("cache_read_tokens", "缓存命中 token"),
        ExportColumn("tool_call_count", "工具调用数"),
        ExportColumn("first_thinking_token_ms", "首思考 token(ms)"),
        ExportColumn("first_answer_token_ms", "首答案 token(ms)"),
        ExportColumn("attempts_made", "尝试次数"),
        ExportColumn("actual_tool_calls", "工具调用明细"),
        ExportColumn("error_message", "错误信息"),
        ExportColumn("langfuse_trace_id", "Langfuse Trace"),
    ]
    # One column per score dimension, sorted for stable output.
    for d in dims:
        columns.append(ExportColumn(f"score::{d}", f"分数·{d}"))

    return build_export_response(rows, columns, format, f"eval_run_{run_id[:8]}_results")


class ExportCompareRequest(BaseModel):
    run_ids: list[str]
    align_key: str = "case_id"  # "case_id" | "question"
    format: str = "csv"


@router.post("/runs/export-compare")
async def export_compare(req: ExportCompareRequest):
    """Export a per-sample cross-run score matrix (mirrors EvaluationComparePage)."""
    validate_format(req.format)
    if not req.run_ids:
        raise HTTPException(status_code=400, detail="run_ids is required")

    # Load every run's results, then align samples across runs by case id or
    # normalized question — same keying the compare page uses client-side.
    run_labels: dict[str, str] = {}
    all_dims: set[str] = set()
    aligned: dict[str, dict[str, Any]] = {}

    for run_id in req.run_ids:
        run_uuid = uuid.UUID(run_id)
        run_row, rows, dims = await _collect_run_results(run_uuid)
        all_dims.update(dims)
        run_labels[run_id] = (
            getattr(run_row, "langfuse_run_name", None) or run_id[:8]
        )
        for r in rows:
            if req.align_key == "question":
                q = (r.get("question") or "").strip()
                if not q:
                    continue
                key = " ".join(q.split()).lower()
                label = q
            else:
                key = r.get("benchmark_case_id") or r.get("test_case_id") or ""
                if not key:
                    continue
                label = (r.get("question") or "")[:120] or key
            slot = aligned.setdefault(key, {"对齐键": key, "样例": label})
            for d, v in (r.get("scores") or {}).items():
                slot[f"{run_id}::{d}"] = v
            slot[f"{run_id}::status"] = r.get("status")

    columns = [ExportColumn("样例", "样例"), ExportColumn("对齐键", "对齐键")]
    for run_id in req.run_ids:
        label = run_labels.get(run_id, run_id[:8])
        columns.append(ExportColumn(f"{run_id}::status", f"{label}·状态"))
        for d in sorted(all_dims):
            columns.append(ExportColumn(f"{run_id}::{d}", f"{label}·{d}"))

    rows = sorted(aligned.values(), key=lambda x: str(x.get("样例", "")))
    return build_export_response(rows, columns, req.format, "eval_compare")


class ExportRunsSummaryRequest(BaseModel):
    run_ids: list[str]
    format: str = "csv"


@router.post("/runs/export-summary")
async def export_runs_summary(req: ExportRunsSummaryRequest):
    """Export per-sample results for the selected runs as csv/json/xlsx.

    The columns match the single-run detail export (`/runs/{id}/results/export`)
    so the batch file is just those rows concatenated across runs — with two
    leading columns (run id + run name) marking which run each row came from.
    The frontend posts the run ids the user checked (which may span pages), and
    we reload each from the DB so the file isn't limited to rows in memory.
    """
    validate_format(req.format)
    if not req.run_ids:
        raise HTTPException(status_code=400, detail="run_ids is required")

    all_dims: set[str] = set()
    rows: list[dict[str, Any]] = []
    for run_id in req.run_ids:
        try:
            run_uuid = uuid.UUID(run_id)
        except ValueError:
            continue
        run_row, run_rows, dims = await _collect_run_results(run_uuid)
        all_dims.update(dims)
        run_name = getattr(run_row, "langfuse_run_name", None) or run_id[:8]
        for r in run_rows:
            rows.append({"run_id": run_id, "run_name": run_name, **r})

    # Same columns as the single-run detail export, prefixed with run id / name.
    columns = [
        ExportColumn("run_id", "运行 ID"),
        ExportColumn("run_name", "运行名"),
        ExportColumn("id", "结果 ID"),
        ExportColumn("benchmark_case_id", "基准用例 ID"),
        ExportColumn("question", "问题"),
        ExportColumn("status", "状态"),
        ExportColumn("actual_output", "实际输出"),
        ExportColumn("latency_ms", "时延(ms)"),
        ExportColumn("prompt_tokens", "输入 token"),
        ExportColumn("completion_tokens", "输出 token"),
        ExportColumn("total_tokens", "总 token"),
        ExportColumn("cache_creation_tokens", "缓存写入 token"),
        ExportColumn("cache_read_tokens", "缓存命中 token"),
        ExportColumn("tool_call_count", "工具调用数"),
        ExportColumn("first_thinking_token_ms", "首思考 token(ms)"),
        ExportColumn("first_answer_token_ms", "首答案 token(ms)"),
        ExportColumn("attempts_made", "尝试次数"),
        ExportColumn("actual_tool_calls", "工具调用明细"),
        ExportColumn("error_message", "错误信息"),
        ExportColumn("langfuse_trace_id", "Langfuse Trace"),
    ]
    # One column per score dimension (union across all selected runs).
    for d in sorted(all_dims):
        columns.append(ExportColumn(f"score::{d}", f"分数·{d}"))

    return build_export_response(rows, columns, req.format, "eval_runs_results")


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str):
    ok = request_stop(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="run not found or already finished")
    return {"run_id": run_id, "status": "stopping"}


@router.post("/runs/{run_id}/reaggregate")
async def reaggregate_run(run_id: str):
    """Recompute summary_scores from existing test_results + evaluation_scores.

    For old runs that finished before we started writing
    ``tool_usage`` / ``score_distribution`` / ``dimension_averages`` into
    ``summary_scores``. Reads what's already in the DB, recomputes the
    aggregates the same way the runner does at finish time, merges them
    into the existing summary_scores so we don't clobber other fields
    (runtime_error, langsmith_project, etc.), and writes back.
    """
    run_uuid = uuid.UUID(run_id)
    async with async_session_factory() as session:
        run_row = (await session.execute(
            select(TestRunRow).where(TestRunRow.id == run_uuid)
        )).scalar_one_or_none()
        if run_row is None:
            raise HTTPException(status_code=404, detail="run not found")

        results = (await session.execute(
            select(TestResultRow).where(TestResultRow.run_id == run_uuid)
        )).scalars().all()
        score_rows = (await session.execute(
            select(EvaluationScoreRow).where(
                EvaluationScoreRow.result_id.in_([r.id for r in results])
            )
        )).scalars().all() if results else []

        # Build per-result score dict
        scores_by_result: dict[uuid.UUID, dict[str, float]] = {}
        for s in score_rows:
            scores_by_result.setdefault(s.result_id, {})[s.dimension] = float(s.score)

        # dimension_averages — mean across all cases that have that dim
        all_scores: dict[str, list[float]] = {}
        for r in results:
            for k, v in scores_by_result.get(r.id, {}).items():
                all_scores.setdefault(k, []).append(v)
        dim_avg = {k: round(sum(vs) / len(vs), 3) for k, vs in all_scores.items() if vs}

        # score_distribution — same buckets the runner uses
        bucket_edges = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0001]
        bucket_labels = ["0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1"]
        score_distribution: dict[str, list[int]] = {}
        for dim, vs in all_scores.items():
            counts = [0] * (len(bucket_edges) - 1)
            for v in vs:
                for i in range(len(counts)):
                    if bucket_edges[i] <= v < bucket_edges[i + 1]:
                        counts[i] += 1
                        break
            score_distribution[dim] = counts

        # tool_usage — from actual_tool_calls JSON column
        tool_stats: dict[str, dict[str, Any]] = {}
        for r in results:
            calls = r.actual_tool_calls or []
            for call in calls:
                if not isinstance(call, dict):
                    continue
                name = call.get("tool_name") or call.get("name") or "unknown"
                slot = tool_stats.setdefault(name, {
                    "name": name, "calls": 0, "errors": 0, "cases": 0,
                })
                slot["calls"] += 1
                out = call.get("output")
                if isinstance(out, dict) and (out.get("error") or out.get("isError")):
                    slot["errors"] += 1
                elif isinstance(out, str) and out.lower().startswith("error"):
                    slot["errors"] += 1
            seen_in_case = {
                (c.get("tool_name") or c.get("name") or "unknown")
                for c in calls if isinstance(c, dict)
            }
            for nm in seen_in_case:
                tool_stats.setdefault(nm, {"name": nm, "calls": 0, "errors": 0, "cases": 0})
                tool_stats[nm]["cases"] += 1
        tool_usage = sorted(tool_stats.values(), key=lambda x: (-x["calls"], x["name"]))

        # cost (success/failure split) — build minimal dicts that
        # _aggregate_cost knows how to read.
        def _cost_row(r):
            return {
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "total_tokens": r.total_tokens,
                "tool_call_count": r.tool_call_count,
                "message_count": None,  # not persisted on test_results
                "cache_creation_tokens": r.cache_creation_tokens,
                "cache_read_tokens": r.cache_read_tokens,
                "latency_ms": r.latency_ms,
                "first_thinking_token_ms": getattr(r, "first_thinking_token_ms", None),
                "first_answer_token_ms": getattr(r, "first_answer_token_ms", None),
            }

        succ = [_cost_row(r) for r in results if r.status == "pass"]
        fail = [_cost_row(r) for r in results if r.status != "pass"]

        merged = dict(run_row.summary_scores or {})
        merged["dimension_averages"] = dim_avg
        merged["score_distribution"] = {
            "buckets": bucket_labels,
            "by_dimension": score_distribution,
        }
        merged["tool_usage"] = tool_usage
        merged["cost_success"] = _aggregate_cost(succ)
        merged["cost_failure"] = _aggregate_cost(fail)
        merged["counts"] = {
            "total": len(results),
            "passed": len(succ),
            "failed": len(fail),
            "unreachable": sum(
                1 for r in results
                if r.status in ("agent_unreachable", "agent_timeout")
            ),
        }

        run_row.summary_scores = merged
        await session.commit()

    return {
        "run_id": run_id,
        "dimensions": list(dim_avg.keys()),
        "tool_usage_count": len(tool_usage),
        "case_count": len(results),
    }


@router.get("/results/{result_id}/trace", response_model=RunDetailResponse)
async def get_result_trace(
    result_id: str,
    project: str | None = Query(default=None, description="Override the run's stored langsmith_project"),
    ext: TraceExtractor = Depends(get_extractor),
):
    """Fetch the LangSmith trace tree for a single eval result.

    Uses the ``langsmith_run_id`` stamped during backfill. The underlying
    extractor is shared with ``/api/traces/run_detail`` — it caches by
    ``(run_id, project)`` for 5 minutes so repeat opens are free.

    The optional ``project`` query parameter lets the detail page point at
    a different LangSmith project than the one bound at start time, so
    users can explore a trace that ended up in another project bucket.
    """
    result_uuid = uuid.UUID(result_id)
    async with async_session_factory() as session:
        result = (await session.execute(
            select(TestResultRow).where(TestResultRow.id == result_uuid)
        )).scalar_one_or_none()
        if result is None:
            raise HTTPException(status_code=404, detail="result not found")
        if not result.langsmith_run_id:
            raise HTTPException(
                status_code=404,
                detail="no langsmith_run_id — backfill may still be running",
            )
        run_row = await Repository(session).get_test_run(result.run_id)
        run_project = run_row.langsmith_project if run_row else None
    target_project = project or run_project
    try:
        data = await ext.get_run_detail(result.langsmith_run_id, project_name=target_project)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LangSmith API error: {e}") from e
    return RunDetailResponse(**data)


@router.post("/runs/{run_id}/backfill_trace")
async def trigger_trace_backfill(
    run_id: str,
    project: str = Query(..., min_length=1, description="LangSmith project to query"),
):
    """Re-run trace backfill against an arbitrary project.

    Looks up every result row of this run, queries LangSmith by
    (project, time window, question text) and stamps the matched
    ``langsmith_run_id`` back to each row. Persists ``project`` to the
    run so the detail page can read it back.
    """
    try:
        stats = await rerun_backfill(run_id=run_id, project=project)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"backfill failed: {e}") from e
    return {"run_id": run_id, "project": project, **stats}


@router.post("/runs/{run_id}/sync_langfuse_scores")
async def trigger_langfuse_score_sync(
    run_id: str,
    push: bool = Query(default=True, description="Re-push our local scores+traces to Langfuse first"),
    pull_attempts: int = Query(default=3, ge=1, le=10),
    pull_interval: int = Query(default=20, ge=5, le=120),
):
    """Manual trigger for the Langfuse score round-trip.

    1. (optional) Re-push our local evaluator scores + traces to Langfuse
    2. Poll ``/api/public/scores`` for ``source=EVAL`` entries (i.e. scores
       produced by Langfuse-configured evaluators) and stamp them back into
       ``evaluation_scores`` with ``dimension="langfuse:<name>"``.

    The post-run pipeline already does this fire-and-forget. Use this
    endpoint when you need to re-pull (e.g. you re-saved the Langfuse
    evaluator config) or sync an older run that ran before remote_write
    was enabled.
    """
    from agent_eval.evaluation.langfuse_sync import (
        pull_evaluator_scores_for_run, sync_run_scores_to_langfuse,
    )
    from sqlalchemy import select
    from agent_eval.db_models.tables import EvaluationScoreRow

    push_stats = None
    if push:
        # Rebuild per_case_results from DB so we don't have to keep the
        # in-memory list around past run completion.
        async with async_session_factory() as session:
            results = (await session.execute(
                select(TestResultRow).where(TestResultRow.run_id == uuid.UUID(run_id))
            )).scalars().all()
            score_rows = []
            if results:
                score_rows = (await session.execute(
                    select(EvaluationScoreRow).where(
                        EvaluationScoreRow.result_id.in_([r.id for r in results])
                    )
                )).scalars().all()
            scores_by_rid: dict[uuid.UUID, dict[str, float]] = {}
            for s in score_rows:
                # Skip langfuse:* — those came FROM Langfuse, no need to re-push.
                if s.dimension.startswith("langfuse:"):
                    continue
                scores_by_rid.setdefault(s.result_id, {})[s.dimension] = float(s.score)
            run = await Repository(session).get_test_run(uuid.UUID(run_id))
        per_case = []
        for r in results:
            per_case.append({
                "case_id": str(r.benchmark_case_id) if r.benchmark_case_id else None,
                "case_name": (r.question or "case")[:50],
                "question": r.question or "",
                "actual_output": r.actual_output or "",
                "thread_id": r.thread_id,
                "status": r.status,
                "latency_ms": r.latency_ms,
                "langsmith_run_id": r.langsmith_run_id,
                "scores": scores_by_rid.get(r.id) or {},
            })
        push_stats = await sync_run_scores_to_langfuse(
            run_id=run_id, run_name=run.langfuse_run_name if run else None,
            per_case_results=per_case,
            # Pull tags out of the run's stored evaluator_configs snapshot
            # so re-syncs preserve the same tag set the run had originally.
            extra_tags=[
                cfg.get("tag") or cfg.get("label")
                for cfg in (run.evaluator_configs or []) if (cfg.get("tag") or cfg.get("label"))
            ] if run else None,
        )

    pull_stats = await pull_evaluator_scores_for_run(
        run_id=run_id, max_attempts=pull_attempts, interval_seconds=pull_interval,
    )
    return {"run_id": run_id, "push": push_stats, "pull": pull_stats}


# ───────────────────────────────────────────────────────────────────────────
# Evaluator instance CRUD
# ───────────────────────────────────────────────────────────────────────────


def _row_to_instance(row) -> EvaluatorInstance:
    return EvaluatorInstance(
        id=str(row.id),
        name=row.name,
        tag=row.tag or row.name,
        evaluator_type=row.evaluator_type,
        description=row.description,
        params=row.params or {},
        is_active=row.is_active,
        current_version_id=str(row.current_version_id) if row.current_version_id else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/evaluators", response_model=list[EvaluatorInstance])
async def list_evaluator_instances(active_only: bool = Query(default=False)):
    async with async_session_factory() as session:
        repo = Repository(session)
        rows = await repo.list_evaluator_configs(active_only=active_only)
    return [_row_to_instance(r) for r in rows]


@router.post("/evaluators", response_model=EvaluatorInstance)
async def create_evaluator_instance(req: CreateEvaluatorRequest):
    # tag-only mode: any string is a valid tag. evaluator_type is kept as
    # an optional free-text label so old runs that referenced it still
    # display sensibly, but it's no longer validated against a registry.
    async with async_session_factory() as session:
        repo = Repository(session)
        try:
            row = await repo.create_evaluator_config(
                name=req.name,
                tag=req.tag or req.name,
                evaluator_type=req.evaluator_type,
                description=req.description, params=req.params,
                is_active=req.is_active,
            )
            # For versioned evaluators (configurable_judge), seed v1 so the
            # editor's "versions" tab has something to show on day one and so
            # runs can pin to a stable evaluator_version_id from the start.
            if req.evaluator_type == "configurable_judge" and req.params:
                version = await repo.create_evaluator_version(
                    evaluator_id=row.id,
                    params=req.params,
                    description="initial version",
                )
                row.current_version_id = version.id
                await session.flush()
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise HTTPException(status_code=400, detail=f"创建失败：{e}") from e
    return _row_to_instance(row)


@router.put("/evaluators/{evaluator_id}", response_model=EvaluatorInstance)
async def update_evaluator_instance(evaluator_id: str, req: UpdateEvaluatorRequest):
    updates = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None}
    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.update_evaluator_config(uuid.UUID(evaluator_id), **updates)
        if row is None:
            raise HTTPException(status_code=404, detail="evaluator not found")
        # If the editor pushed a new params payload onto a configurable_judge
        # evaluator, append it as a new version and route future runs to it.
        # Tag/name/active changes don't bump a version — those are display-only.
        new_params = updates.get("params")
        if (
            row.evaluator_type == "configurable_judge"
            and isinstance(new_params, dict)
            and new_params
        ):
            version = await repo.create_evaluator_version(
                evaluator_id=row.id,
                params=new_params,
            )
            row.current_version_id = version.id
            await session.flush()
        await session.commit()
    return _row_to_instance(row)


@router.delete("/evaluators/{evaluator_id}")
async def delete_evaluator_instance(evaluator_id: str):
    async with async_session_factory() as session:
        repo = Repository(session)
        ok = await repo.delete_evaluator_config(uuid.UUID(evaluator_id))
        if not ok:
            raise HTTPException(status_code=404, detail="evaluator not found")
        await session.commit()
    return {"id": evaluator_id, "deleted": True}


# ───────────────────────────────────────────────────────────────────────────
# Configurable judge dry-run (PR-B)
# ───────────────────────────────────────────────────────────────────────────


@router.post("/evaluators/{evaluator_id}/dry-run", response_model=DryRunResponse)
async def dry_run_evaluator(evaluator_id: str, req: DryRunRequest):
    """Score one (input, output, expected) tuple using the params being
    drafted in the editor — without saving a new version.

    The body wins over the saved row: ``params`` from the request becomes
    the judge config, and ``provider_id`` (when given) overrides whatever
    is in ``params['provider_id']``. This lets the user try an unsaved
    prompt against a different provider in one click.
    """
    from agent_eval.evaluation.configurable_judge import run_configurable_judge

    async with async_session_factory() as session:
        repo = Repository(session)
        evaluator_row = await repo.get_evaluator_config(uuid.UUID(evaluator_id))
        if evaluator_row is None:
            raise HTTPException(status_code=404, detail="evaluator not found")

        params = dict(req.params or evaluator_row.params or {})
        provider_id = req.provider_id or params.get("provider_id")
        if not provider_id:
            raise HTTPException(
                status_code=400,
                detail="provider_id is required (in body or params['provider_id'])",
            )
        try:
            provider_uuid = uuid.UUID(provider_id)
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"invalid provider_id: {e}") from e

        provider_row = await repo.get_evaluator_provider(provider_uuid)
        if provider_row is None:
            raise HTTPException(status_code=404, detail="provider not found")

    result = await run_configurable_judge(
        params=params,
        provider=provider_row,
        input_text=req.input,
        output_text=req.output,
        expected_output=req.expected_output,
        metadata=req.metadata,
        evaluator_name=evaluator_row.name or "score",
    )
    return DryRunResponse(
        scores=[
            DryRunScoreItem(
                name=s.name,
                value=s.value,
                reason=s.reason,
                raw_value=s.raw_value,
            )
            for s in result.scores
        ],
        model=result.model,
        usage={
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "total_tokens": result.usage.total_tokens,
        },
        raw_content=result.raw_content,
        rendered_messages=result.rendered_messages,
        error=result.error,
    )


# ───────────────────────────────────────────────────────────────────────────
# Evaluator versions (PR-C)
# ───────────────────────────────────────────────────────────────────────────


def _row_to_version(row) -> EvaluatorVersion:
    return EvaluatorVersion(
        id=str(row.id),
        evaluator_id=str(row.evaluator_id),
        version_number=row.version_number,
        params=row.params or {},
        description=row.description,
        created_by=str(row.created_by) if row.created_by else None,
        created_at=row.created_at,
    )


@router.get("/evaluators/{evaluator_id}/versions", response_model=list[EvaluatorVersion])
async def list_evaluator_versions(evaluator_id: str):
    """Versions newest-first. Caller cross-references against the
    evaluator's ``current_version_id`` to render the active row."""
    async with async_session_factory() as session:
        repo = Repository(session)
        evaluator_row = await repo.get_evaluator_config(uuid.UUID(evaluator_id))
        if evaluator_row is None:
            raise HTTPException(status_code=404, detail="evaluator not found")
        rows = await repo.list_evaluator_versions(uuid.UUID(evaluator_id))
    return [_row_to_version(r) for r in rows]


@router.post("/evaluators/{evaluator_id}/versions", response_model=EvaluatorVersion)
async def create_evaluator_version(
    evaluator_id: str, req: CreateEvaluatorVersionRequest,
):
    """Append a new version snapshot. With ``activate=true`` (default) also
    routes future invocations to it. The PUT /evaluators/{id} flow already
    auto-bumps versions when ``params`` change, so this endpoint is for
    explicit "save as new version without changing other fields" workflows."""
    async with async_session_factory() as session:
        repo = Repository(session)
        evaluator_row = await repo.get_evaluator_config(uuid.UUID(evaluator_id))
        if evaluator_row is None:
            raise HTTPException(status_code=404, detail="evaluator not found")
        try:
            version = await repo.create_evaluator_version(
                evaluator_id=uuid.UUID(evaluator_id),
                params=req.params,
                description=req.description,
            )
            if req.activate:
                await repo.set_current_evaluator_version(
                    uuid.UUID(evaluator_id), version.id,
                )
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise HTTPException(status_code=400, detail=f"创建版本失败：{e}") from e
    return _row_to_version(version)


@router.post(
    "/evaluators/{evaluator_id}/versions/{version_id}/activate",
    response_model=EvaluatorInstance,
)
async def activate_evaluator_version(evaluator_id: str, version_id: str):
    """Make ``version_id`` the active version, copying its params back onto
    the evaluator row. Returns the updated evaluator so the editor can
    refresh its form state in one round-trip."""
    async with async_session_factory() as session:
        repo = Repository(session)
        try:
            row = await repo.set_current_evaluator_version(
                uuid.UUID(evaluator_id), uuid.UUID(version_id),
            )
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"invalid id: {e}") from e
        if row is None:
            raise HTTPException(
                status_code=404,
                detail="evaluator or version not found, or version belongs to another evaluator",
            )
        await session.commit()
    return _row_to_instance(row)


# ───────────────────────────────────────────────────────────────────────────
# Upload test case files (JSON / JSONL)
# ───────────────────────────────────────────────────────────────────────────


def _parse_cases_payload(raw: bytes, filename: str) -> tuple[list[dict[str, Any]], str]:
    """Return (cases, file_format). Accepts both JSON (with or without
    ``test_cases`` wrapper) and JSONL (one object per line)."""
    text = raw.decode("utf-8-sig").strip()
    if not text:
        raise ValueError("empty file")

    lower = filename.lower()
    if lower.endswith(".jsonl"):
        cases = []
        for i, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"JSONL line {i+1} invalid: {e}") from e
        return cases, "jsonl"

    # Default: JSON
    data = json.loads(text)
    if isinstance(data, list):
        return data, "json"
    if isinstance(data, dict):
        if "test_cases" in data and isinstance(data["test_cases"], list):
            return data["test_cases"], "json"
        if "cases" in data and isinstance(data["cases"], list):
            return data["cases"], "json"
    raise ValueError(
        "JSON must be a list, or an object with a 'test_cases' / 'cases' array"
    )


def _normalize_cases(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce each entry into {name, question, expected_keywords} shape."""
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        # Accept several aliases
        question = (
            item.get("question")
            or item.get("input")
            or item.get("prompt")
            or item.get("content")
            or ""
        )
        if not question and isinstance(item.get("messages"), list):
            for m in reversed(item["messages"]):
                if isinstance(m, dict) and m.get("role") == "user":
                    question = m.get("content", "")
                    break
        if not question:
            continue
        out.append({
            "name": str(item.get("name") or item.get("id") or f"case-{i+1}"),
            "question": question,
            "expected_keywords": item.get("expected_keywords") or [],
            "expected_output": item.get("expected_output") or item.get("reference_answer") or "",
            "metadata": item.get("metadata") or {},
        })
    return out


@router.post("/case_sources/upload", response_model=UploadCasesResponse)
async def upload_cases(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        parsed, file_format = _parse_cases_payload(raw, file.filename or "upload.json")
        cases = _normalize_cases(parsed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not cases:
        raise HTTPException(status_code=400, detail="no valid cases parsed")
    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.create_eval_case_source(
            name=file.filename or "upload", source_kind="file",
            file_format=file_format, cases=cases,
        )
        await session.commit()
    return UploadCasesResponse(
        source_id=str(row.id), name=row.name, count=len(cases),
        preview=cases[:3],
    )


@router.get("/case_sources", response_model=list[EvalCaseSourceSummary])
async def list_case_sources():
    async with async_session_factory() as session:
        repo = Repository(session)
        rows = await repo.list_eval_case_sources(limit=50)
    return [
        EvalCaseSourceSummary(
            id=str(r.id), name=r.name, source_kind=r.source_kind,
            file_format=r.file_format, count=len(r.cases or []),
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/case_sources/{source_id}")
async def get_case_source(source_id: str):
    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.get_eval_case_source(uuid.UUID(source_id))
        if row is None:
            raise HTTPException(status_code=404, detail="source not found")
    return {
        "id": str(row.id),
        "name": row.name,
        "file_format": row.file_format,
        "cases": row.cases,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
