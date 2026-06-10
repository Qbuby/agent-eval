from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.auth.dependencies import (
    ROLE_ADMIN,
    get_current_user,
    require_role,
)
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import BenchmarkCaseRow, CategoryRow, ProjectRow

# Router-level login gate: every endpoint requires an authenticated user.
# Preserves the auth.enabled bypass (get_current_user returns None when auth is
# disabled). Destructive writes additionally require admin via require_role.
router = APIRouter(
    prefix="/api/projects",
    tags=["projects"],
    dependencies=[Depends(get_current_user)],
)


class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""


class CreateCategoryRequest(BaseModel):
    name: str
    description: str = ""


class UpdateCategoryRequest(BaseModel):
    name: str | None = None
    description: str | None = None


@router.get("")
async def list_projects():
    async with async_session_factory() as session:
        result = await session.execute(select(ProjectRow).order_by(ProjectRow.name))
        projects = result.scalars().all()
    return [
        {"id": str(p.id), "name": p.name, "description": p.description, "created_at": p.created_at}
        for p in projects
    ]


@router.post("")
async def create_project(req: CreateProjectRequest):
    async with async_session_factory() as session:
        existing = await session.execute(select(ProjectRow).where(ProjectRow.name == req.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Project '{req.name}' already exists")
        row = ProjectRow(name=req.name, description=req.description)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return {"id": str(row.id), "name": row.name}


@router.get("/{project_id}/categories")
async def list_categories(project_id: str):
    async with async_session_factory() as session:
        result = await session.execute(
            select(CategoryRow).where(CategoryRow.project_id == project_id).order_by(CategoryRow.name)
        )
        cats = result.scalars().all()
    return [
        {"id": str(c.id), "name": c.name, "description": c.description, "created_at": c.created_at}
        for c in cats
    ]


@router.post("/{project_id}/categories")
async def create_category(project_id: str, req: CreateCategoryRequest):
    async with async_session_factory() as session:
        existing = await session.execute(
            select(CategoryRow).where(
                CategoryRow.project_id == project_id,
                CategoryRow.name == req.name,
            )
        )
        row = existing.scalar_one_or_none()
        if row:
            return {"id": str(row.id), "name": row.name}
        row = CategoryRow(project_id=project_id, name=req.name, description=req.description)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return {"id": str(row.id), "name": row.name}


@router.put("/categories/{category_id}")
async def update_category(category_id: str, req: UpdateCategoryRequest):
    async with async_session_factory() as session:
        result = await session.execute(
            select(CategoryRow).where(CategoryRow.id == category_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Category not found")

        if req.name is not None:
            row.name = req.name
        if req.description is not None:
            row.description = req.description

        await session.commit()
    return {"id": str(row.id), "name": row.name}


@router.delete("/categories/{category_id}", dependencies=[Depends(require_role(ROLE_ADMIN))])
async def delete_category(category_id: str):
    async with async_session_factory() as session:
        count_result = await session.execute(
            select(func.count()).where(BenchmarkCaseRow.category_id == category_id)
        )
        case_count = count_result.scalar() or 0
        if case_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete: category has {case_count} benchmark cases. Remove them first.",
            )

        result = await session.execute(
            select(CategoryRow).where(CategoryRow.id == category_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Category not found")

        await session.delete(row)
        await session.commit()
    return {"deleted": category_id}
