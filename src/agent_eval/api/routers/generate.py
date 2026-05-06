from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agent_eval.api.dependencies import get_generator, get_manager
from agent_eval.api.schemas import GenerateMutateRequest, GenerateScenarioRequest
from agent_eval.data.case_generator import CaseGenerator
from agent_eval.data.dataset_manager import DatasetManager

router = APIRouter(prefix="/api/generate", tags=["generate"])


@router.post("/scenario")
async def generate_from_scenario(
    req: GenerateScenarioRequest,
    gen: CaseGenerator = Depends(get_generator),
    mgr: DatasetManager = Depends(get_manager),
):
    cases = await gen.generate_from_scenario(
        req.scenario, count=req.count, context=req.context,
        tags=req.tags or None,
    )
    if not cases:
        raise HTTPException(status_code=422, detail="LLM returned no valid cases")

    result = [c.model_dump(mode="json", exclude_none=True) for c in cases]

    if not req.dry_run:
        await mgr.add_cases_batch(req.dataset, cases, split=req.split)

    return {"generated": len(cases), "saved": not req.dry_run, "cases": result}


@router.post("/mutate")
async def generate_mutations(
    req: GenerateMutateRequest,
    gen: CaseGenerator = Depends(get_generator),
    mgr: DatasetManager = Depends(get_manager),
):
    all_cases = await mgr.load_cases(req.dataset)
    source_case = next((c for c in all_cases if c.id.startswith(req.case_id)), None)
    if not source_case:
        raise HTTPException(status_code=404, detail=f"Case '{req.case_id}' not found")

    cases = await gen.generate_mutations(
        source_case, count=req.count, strategy=req.strategy,
        tags=req.tags or None,
    )
    if not cases:
        raise HTTPException(status_code=422, detail="LLM returned no valid cases")

    result = [c.model_dump(mode="json", exclude_none=True) for c in cases]

    if not req.dry_run:
        target = req.target_dataset or req.dataset
        await mgr.add_cases_batch(target, cases, split=req.split)

    return {"generated": len(cases), "saved": not req.dry_run, "cases": result}
