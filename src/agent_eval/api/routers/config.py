from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from agent_eval.auth.dependencies import get_current_user, require_admin
from agent_eval.config_service import ConfigService, config_service
from agent_eval.db_models.tables import UserRow

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigOption(BaseModel):
    value: Any
    label: str | None = None


class ConfigItemResponse(BaseModel):
    key: str
    value: Any  # default option's value (back-compat)
    options: list[ConfigOption]
    default_index: int
    category: str
    description: str | None
    updated_by: uuid.UUID | None
    updated_at: str | None

    model_config = {"from_attributes": True}


class ConfigUpdateRequest(BaseModel):
    """Replace all options with a single value (back-compat single-value PUT)."""
    value: Any
    description: str | None = None


class AddOptionRequest(BaseModel):
    value: Any
    label: str | None = None
    make_default: bool = False
    description: str | None = None


class UpdateOptionRequest(BaseModel):
    value: Any
    label: str | None = None


class SetDefaultRequest(BaseModel):
    index: int


class BatchUpdateRequest(BaseModel):
    items: dict[str, Any]


def _to_response(row) -> ConfigItemResponse:
    options, default_index = ConfigService.normalize_options(row.value)
    default_value = options[default_index]["value"] if options else None
    return ConfigItemResponse(
        key=row.key,
        value=default_value,
        options=[ConfigOption(value=o["value"], label=o.get("label")) for o in options],
        default_index=default_index,
        category=row.category,
        description=row.description,
        updated_by=row.updated_by,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


def _guard_sensitive(key: str, action: str = "accessed") -> None:
    if ConfigService.is_sensitive(key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Sensitive config cannot be {action} via API",
        )


# ─── Routes — order matters: more-specific paths must come before {key:path}.

@router.get("", response_model=list[ConfigItemResponse])
async def list_configs(
    category: str | None = None,
    _user: UserRow | None = Depends(get_current_user),
):
    rows = await config_service.list(category=category)
    return [_to_response(r) for r in rows if not ConfigService.is_sensitive(r.key)]


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


# Options endpoints — declared BEFORE the {key:path} catch-all routes so
# FastAPI matches `foo/options/0` here instead of swallowing it as a key.

@router.post("/options/{key:path}", response_model=ConfigItemResponse)
async def add_option(
    key: str,
    body: AddOptionRequest,
    admin: UserRow = Depends(require_admin),
):
    _guard_sensitive(key, "modified")
    row = await config_service.add_option(
        key,
        body.value,
        label=body.label,
        make_default=body.make_default,
        user_id=admin.id,
        description=body.description,
    )
    return _to_response(row)


@router.put("/options/{index}/{key:path}", response_model=ConfigItemResponse)
async def update_option(
    key: str,
    index: int,
    body: UpdateOptionRequest,
    admin: UserRow = Depends(require_admin),
):
    _guard_sensitive(key, "modified")
    try:
        row = await config_service.update_option(
            key, index, body.value, label=body.label, user_id=admin.id,
        )
    except IndexError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return _to_response(row)


@router.delete("/options/{index}/{key:path}", response_model=ConfigItemResponse)
async def remove_option(
    key: str,
    index: int,
    admin: UserRow = Depends(require_admin),
):
    _guard_sensitive(key, "modified")
    try:
        row = await config_service.remove_option(key, index, user_id=admin.id)
    except IndexError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _to_response(row)


@router.put("/default/{index}/{key:path}", response_model=ConfigItemResponse)
async def set_default_option(
    key: str,
    index: int,
    admin: UserRow = Depends(require_admin),
):
    _guard_sensitive(key, "modified")
    try:
        row = await config_service.set_default(key, index, user_id=admin.id)
    except IndexError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return _to_response(row)


# Catch-all single-key routes — keep these last.

@router.get("/{key:path}", response_model=ConfigItemResponse)
async def get_config(
    key: str,
    _user: UserRow | None = Depends(get_current_user),
):
    _guard_sensitive(key, "accessed")

    from agent_eval.db import async_session_factory
    from sqlalchemy import select
    from agent_eval.db_models.tables import SystemConfigRow

    async with async_session_factory() as session:
        result = await session.execute(
            select(SystemConfigRow).where(SystemConfigRow.key == key)
        )
        row = result.scalar_one_or_none()

    if row is not None:
        return _to_response(row)

    value = await config_service.get(key)
    if value is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Config '{key}' not found")

    return ConfigItemResponse(
        key=key,
        value=value,
        options=[ConfigOption(value=value, label=None)],
        default_index=0,
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
    """Single-value replace — collapses options to one entry. Use the
    /options endpoints to manage multi-value entries."""
    _guard_sensitive(key, "modified")
    row = await config_service.set(key, body.value, user_id=admin.id, description=body.description)
    return _to_response(row)


@router.delete("/{key:path}")
async def delete_config(
    key: str,
    _admin: UserRow = Depends(require_admin),
):
    _guard_sensitive(key, "deleted")
    deleted = await config_service.delete(key)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Config '{key}' not found")
    return {"detail": "deleted", "key": key}
