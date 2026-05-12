from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agent_eval.api.dependencies import get_extractor, get_manager
from agent_eval.api.schemas import (
    ExtractRequest,
    FillModelsRequest,
    FillModelsResponse,
    ImportTracesRequest,
    ListRunsRequest,
    PullDatasetRequest,
    RunDetailRequest,
    RunDetailResponse,
    RunSummaryResponse,
)
from agent_eval.data.dataset_manager import DatasetManager
from agent_eval.data.trace_extractor import TraceExtractor

router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.post("/runs")
async def list_runs(
    req: ListRunsRequest,
    ext: TraceExtractor = Depends(get_extractor),
):
    try:
        runs = await ext.list_runs(
            req.project_name,
            start_time=req.start_time,
            end_time=req.end_time,
            status=req.status,
            tags=req.tags,
            limit=req.limit,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LangSmith API error: {e}") from e
    items = [
        RunSummaryResponse(
            id=r.id, name=r.name, status=r.status, start_time=r.start_time,
            latency_s=r.latency_s, total_tokens=r.total_tokens, error=r.error,
            tags=r.tags, input_preview=r.input_preview, output_preview=r.output_preview,
            model_name=r.model_name, first_token_s=r.first_token_s,
        )
        for r in runs
    ]
    total = len(items)
    start = (req.page - 1) * req.page_size
    end = start + req.page_size
    page_items = items[start:end]
    return {"items": page_items, "total": total, "page": req.page, "page_size": req.page_size}


@router.post("/extract")
async def extract_cases(
    req: ExtractRequest,
    ext: TraceExtractor = Depends(get_extractor),
):
    cases = await ext.extract_test_cases(
        req.run_ids, source=req.source,
        default_tags=req.default_tags or None,
        include_output_as_expected=req.include_output_as_expected,
    )
    return {"extracted": len(cases), "cases": [c.model_dump(mode="json", exclude_none=True) for c in cases]}


@router.post("/import")
async def import_traces(
    req: ImportTracesRequest,
    ext: TraceExtractor = Depends(get_extractor),
    mgr: DatasetManager = Depends(get_manager),
):
    try:
        if req.project_name:
            cases = await ext.extract_test_cases_fast(
                req.project_name,
                req.run_ids,
                source=req.source,
                default_tags=req.default_tags or None,
                include_output_as_expected=req.include_output_as_expected,
            )
        else:
            cases = await ext.extract_test_cases(
                req.run_ids, source=req.source,
                default_tags=req.default_tags or None,
                include_output_as_expected=req.include_output_as_expected,
            )
        ids = await mgr.add_cases_batch(req.dataset, cases, split=req.split, source_run_ids=req.run_ids)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LangSmith API error: {e}") from e
    return {"imported": len(cases), "ids": ids}


@router.post("/pull")
async def pull_dataset(
    req: PullDatasetRequest,
    mgr: DatasetManager = Depends(get_manager),
):
    cases = await mgr.pull_external_dataset(
        req.source_dataset,
        target_dataset_name=req.target_dataset,
        split=req.split,
        limit=req.limit,
    )
    return {
        "pulled": len(cases),
        "saved_to": req.target_dataset,
        "cases": [c.model_dump(mode="json", exclude_none=True) for c in cases],
    }


@router.post("/run_detail", response_model=RunDetailResponse)
async def run_detail(
    req: RunDetailRequest,
    ext: TraceExtractor = Depends(get_extractor),
):
    try:
        data = await ext.get_run_detail(req.run_id, project_name=req.project_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LangSmith API error: {e}") from e
    return RunDetailResponse(**data)


@router.post("/fill_models", response_model=FillModelsResponse)
async def fill_models(
    req: FillModelsRequest,
    ext: TraceExtractor = Depends(get_extractor),
):
    """Thorough model_name resolution for a list of root run ids.

    Slow (~30-75s cold path) vs. /runs (~23s), so the UI invokes it only on
    explicit user action ("Fill models"). Results are cached for 1 hour, so
    repeat calls and later /runs of the same project return instantly.
    """
    try:
        models, missing = await ext.fill_models(req.project_name, req.runs)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LangSmith API error: {e}") from e
    return FillModelsResponse(models=models, missing=missing)
