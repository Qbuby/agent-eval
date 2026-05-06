from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from agent_eval.api.dependencies import get_manager
from agent_eval.api.schemas import AddCasesRequest, BatchDeleteRequest, TestCaseInput
from agent_eval.data.dataset_manager import DatasetManager
from agent_eval.data.schemas import validate_and_parse
from agent_eval.models.test_case import TestCase

router = APIRouter(tags=["cases"])


@router.get("/api/datasets/{name}/cases")
async def list_cases(
    name: str,
    split: str | None = Query(None),
    tag: list[str] | None = Query(None),
    as_of: str | None = Query(None),
    limit: int | None = Query(None),
    mgr: DatasetManager = Depends(get_manager),
):
    as_of_dt = datetime.fromisoformat(as_of) if as_of else None
    cases = await mgr.load_cases(
        name, as_of=as_of_dt, splits=[split] if split else None,
        tags=tag, limit=limit,
    )
    return [c.model_dump(mode="json", exclude_none=True) for c in cases]


@router.post("/api/datasets/{name}/cases")
async def add_cases(
    name: str,
    req: AddCasesRequest,
    mgr: DatasetManager = Depends(get_manager),
):
    raw_dicts = [c.model_dump(exclude_none=True) for c in req.cases]
    result = validate_and_parse(raw_dicts)
    if result.errors:
        raise HTTPException(status_code=422, detail=result.errors)

    if len(result.cases) == 1:
        ex_id = await mgr.add_case(name, result.cases[0], split=req.split)
        return {"added": 1, "ids": [ex_id]}

    ids = await mgr.add_cases_batch(name, result.cases, split=req.split)
    return {"added": len(result.cases), "ids": ids}


@router.put("/api/cases/{example_id}")
async def update_case(
    example_id: str,
    req: TestCaseInput,
    mgr: DatasetManager = Depends(get_manager),
):
    raw = req.model_dump(exclude_none=True)
    result = validate_and_parse(raw)
    if result.errors:
        raise HTTPException(status_code=422, detail=result.errors)
    await mgr.update_case(example_id, result.cases[0])
    return {"updated": example_id}


@router.delete("/api/cases/{example_id}")
async def delete_case(
    example_id: str,
    mgr: DatasetManager = Depends(get_manager),
):
    await mgr.delete_case(example_id)
    return {"deleted": example_id}


@router.post("/api/cases/batch-delete")
async def batch_delete_cases(
    req: BatchDeleteRequest,
    mgr: DatasetManager = Depends(get_manager),
):
    await mgr.delete_cases_batch(req.example_ids)
    return {"deleted": len(req.example_ids)}
