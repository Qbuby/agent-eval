from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from agent_eval.api.dependencies import get_manager
from agent_eval.api.schemas import AddCasesRequest, BatchDeleteRequest, TestCaseInput
from agent_eval.auth.dependencies import ROLE_ADMIN, get_current_user, require_role
from agent_eval.data.dataset_manager import DatasetManager
from agent_eval.data.schemas import validate_and_parse
from agent_eval.governance.helpers import log_audit
from agent_eval.models.test_case import TestCase

# All case endpoints require an authenticated user (login-only baseline).
router = APIRouter(tags=["cases"], dependencies=[Depends(get_current_user)])


@router.get("/api/datasets/{name}/cases")
async def list_cases(
    name: str,
    split: str | None = Query(None),
    tag: list[str] | None = Query(None),
    as_of: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="按 name/description 模糊搜索"),
    mgr: DatasetManager = Depends(get_manager),
):
    as_of_dt = datetime.fromisoformat(as_of) if as_of else None
    try:
        cases = await mgr.load_cases(
            name, as_of=as_of_dt, splits=[split] if split else None,
            tags=tag,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LangSmith API error: {e}") from e

    if search:
        search_lower = search.lower()
        cases = [
            c for c in cases
            if search_lower in c.name.lower() or search_lower in (c.description or "").lower()
        ]

    total = len(cases)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = cases[start:end]

    return {
        "items": [c.model_dump(mode="json", exclude_none=True) for c in page_items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


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
        await log_audit("example", ex_id, "create", details={"dataset": name})
        return {"added": 1, "ids": [ex_id]}

    ids = await mgr.add_cases_batch(name, result.cases, split=req.split)
    await log_audit("example", name, "import", details={"count": len(ids), "ids": ids[:10]})
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
    await log_audit("example", example_id, "update")
    return {"updated": example_id}


@router.delete("/api/cases/{example_id}", dependencies=[Depends(require_role(ROLE_ADMIN))])
async def delete_case(
    example_id: str,
    mgr: DatasetManager = Depends(get_manager),
):
    await mgr.delete_case(example_id)
    await log_audit("example", example_id, "delete")
    return {"deleted": example_id}


@router.post("/api/cases/batch-delete", dependencies=[Depends(require_role(ROLE_ADMIN))])
async def batch_delete_cases(
    req: BatchDeleteRequest,
    mgr: DatasetManager = Depends(get_manager),
):
    await mgr.delete_cases_batch(req.example_ids)
    await log_audit("example", "batch", "delete", details={"count": len(req.example_ids), "ids": req.example_ids[:10]})
    return {"deleted": len(req.example_ids)}
