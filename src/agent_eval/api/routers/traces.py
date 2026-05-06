from __future__ import annotations

from fastapi import APIRouter, Depends

from agent_eval.api.dependencies import get_extractor, get_manager
from agent_eval.api.schemas import (
    ExtractRequest,
    ImportTracesRequest,
    ListRunsRequest,
    PullDatasetRequest,
    RunSummaryResponse,
)
from agent_eval.data.dataset_manager import DatasetManager
from agent_eval.data.trace_extractor import TraceExtractor

router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.post("/runs", response_model=list[RunSummaryResponse])
async def list_runs(
    req: ListRunsRequest,
    ext: TraceExtractor = Depends(get_extractor),
):
    runs = await ext.list_runs(
        req.project_name,
        start_time=req.start_time,
        end_time=req.end_time,
        status=req.status,
        tags=req.tags,
        limit=req.limit,
    )
    return [
        RunSummaryResponse(
            id=r.id, name=r.name, status=r.status, start_time=r.start_time,
            latency_s=r.latency_s, total_tokens=r.total_tokens, error=r.error,
            tags=r.tags, input_preview=r.input_preview, output_preview=r.output_preview,
        )
        for r in runs
    ]


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
    cases = await ext.extract_test_cases(
        req.run_ids, source=req.source,
        default_tags=req.default_tags or None,
        include_output_as_expected=req.include_output_as_expected,
    )
    ids = await mgr.add_cases_batch(req.dataset, cases, split=req.split, source_run_ids=req.run_ids)
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
