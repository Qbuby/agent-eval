from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from agent_eval.auth.dependencies import get_current_user, require_admin
from agent_eval.config_service import ConfigService, config_service
from agent_eval.db_models.tables import UserRow

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigItemResponse(BaseModel):
    key: str
    value: Any
    category: str
    description: str | None
    updated_by: uuid.UUID | None
    updated_at: str | None

    model_config = {"from_attributes": True}


class ConfigUpdateRequest(BaseModel):
    value: Any
    description: str | None = None


class BatchUpdateRequest(BaseModel):
    items: dict[str, Any]


def _to_response(row) -> ConfigItemResponse:
    value = row.value.get("v") if isinstance(row.value, dict) else row.value
    return ConfigItemResponse(
        key=row.key,
        value=value,
        category=row.category,
        description=row.description,
        updated_by=row.updated_by,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.get("", response_model=list[ConfigItemResponse])
async def list_configs(
    category: str | None = None,
    _user: UserRow | None = Depends(get_current_user),
):
    rows = await config_service.list(category=category)
    return [_to_response(r) for r in rows if not ConfigService.is_sensitive(r.key)]


@router.get("/{key:path}", response_model=ConfigItemResponse)
async def get_config(
    key: str,
    _user: UserRow | None = Depends(get_current_user),
):
    if ConfigService.is_sensitive(key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sensitive config not accessible via API")

    rows = await config_service.list()
    for r in rows:
        if r.key == key:
            return _to_response(r)

    value = await config_service.get(key)
    if value is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Config '{key}' not found")

    return ConfigItemResponse(
        key=key,
        value=value,
        category=key.split(".")[0] if "." in key else "general",
        description=None,
        updated_by=None,
        updated_at=None,
    )


@router.put("/{key:path}", response_model=ConfigItemResponse)
async def update_config(
    key: str,
    body: ConfigUpdateRequest,
    admin: UserRow = Depends(require_admin),
):
    if ConfigService.is_sensitive(key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sensitive config cannot be modified via API")

    row = await config_service.set(key, body.value, user_id=admin.id)
    if body.description is not None:
        from agent_eval.db import async_session_factory
        from sqlalchemy import select
        from agent_eval.db_models.tables import SystemConfigRow

        async with async_session_factory() as session:
            result = await session.execute(
                select(SystemConfigRow).where(SystemConfigRow.key == key)
            )
            db_row = result.scalar_one_or_none()
            if db_row:
                db_row.description = body.description
                await session.commit()
                await session.refresh(db_row)
                row = db_row

    return _to_response(row)


@router.delete("/{key:path}")
async def delete_config(
    key: str,
    _admin: UserRow = Depends(require_admin),
):
    if ConfigService.is_sensitive(key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sensitive config cannot be deleted via API")

    deleted = await config_service.delete(key)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Config '{key}' not found")
    return {"detail": "deleted", "key": key}


@router.post("/batch", response_model=list[ConfigItemResponse])
async def batch_update(
    body: BatchUpdateRequest,
    admin: UserRow = Depends(require_admin),
):
    for key in body.items:
        if ConfigService.is_sensitive(key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Sensitive config '{key}' cannot be modified via API",
            )

    rows = await config_service.batch_set(body.items, user_id=admin.id)
    return [_to_response(r) for r in rows]
