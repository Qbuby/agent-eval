"""HTTP API for the evaluation workbench."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select

from agent_eval.api.dependencies import get_extractor
from agent_eval.api.schemas import (
    BuiltinEvaluator,
    CreateEvaluatorRequest,
    EvalCaseSourceSummary,
    EvalRunDetail,
    EvalRunSummary,
    EvalResultRow,
    EvalResultsPage,
    EvaluatorInstance,
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
)
from agent_eval.evaluation.langfuse_runner import (
    BUILTIN_EVALUATORS,
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
                "evaluator_type": row.evaluator_type,
                "params": row.params or {},
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
            question=r.question,
            latency_ms=r.latency_ms,
            total_tokens=r.total_tokens,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            tool_call_count=r.tool_call_count,
            error_message=r.error_message,
            langfuse_trace_id=r.langfuse_trace_id,
            langsmith_run_id=r.langsmith_run_id,
            scores=score_index.get(r.id, {}),
        ))
    return EvalResultsPage(items=items, total=total, page=page, page_size=page_size)


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str):
    ok = request_stop(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="run not found or already finished")
    return {"run_id": run_id, "status": "stopping"}


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
        evaluator_type=row.evaluator_type,
        description=row.description,
        params=row.params or {},
        is_active=row.is_active,
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
    if req.evaluator_type not in BUILTIN_EVALUATORS:
        raise HTTPException(
            status_code=400,
            detail=f"evaluator_type must be one of {list(BUILTIN_EVALUATORS.keys())}",
        )
    async with async_session_factory() as session:
        repo = Repository(session)
        try:
            row = await repo.create_evaluator_config(
                name=req.name, evaluator_type=req.evaluator_type,
                description=req.description, params=req.params,
                is_active=req.is_active,
            )
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
