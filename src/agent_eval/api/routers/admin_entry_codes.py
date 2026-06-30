"""Admin 入口码管理端点（入口码功能）。

全部挂 ``require_role(ROLE_ADMIN)`` —— 仅内部 admin 可建/改/删入口码。注册时
``auth.register`` 凭码绑定用户到码所指的租户 + 角色。查询用
``async with async_session_factory()`` 直连（仿 admin_tenants）。

entry_codes 表不挂 TenantMixin（全局维度表），admin 是 superadmin，监听器对其
读查询整体旁路，所以这里查 EntryCodeRow / TenantRow 天然全可见。code 明文返回
（admin 需查看并分发给客户）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agent_eval.auth.dependencies import ROLE_ADMIN, ROLE_EXTERNAL, ROLE_USER, require_role
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import EntryCodeRow, TenantRow, UserRow

router = APIRouter(
    prefix="/api/admin/entry-codes",
    tags=["admin-entry-codes"],
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)

_ALLOWED_ROLES = (ROLE_ADMIN, ROLE_USER, ROLE_EXTERNAL)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EntryCodeCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    tenant_id: uuid.UUID
    role: str = Field(..., max_length=32)
    label: str | None = Field(None, max_length=128)


class EntryCodeUpdateRequest(BaseModel):
    """改码值 / 改角色 / 改描述 / 启停。"""

    code: str | None = Field(None, min_length=1, max_length=64)
    role: str | None = Field(None, max_length=32)
    label: str | None = Field(None, max_length=128)
    is_active: bool | None = None


class EntryCodeResponse(BaseModel):
    id: uuid.UUID
    code: str
    tenant_id: uuid.UUID
    role: str
    label: str | None
    is_active: bool
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


def _to_response(c: EntryCodeRow) -> EntryCodeResponse:
    return EntryCodeResponse(
        id=c.id,
        code=c.code,
        tenant_id=c.tenant_id,
        role=c.role,
        label=c.label,
        is_active=c.is_active,
        created_by=c.created_by,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=EntryCodeResponse, status_code=status.HTTP_201_CREATED)
async def create_entry_code(
    body: EntryCodeCreateRequest,
    admin: UserRow = Depends(require_role(ROLE_ADMIN)),
):
    """建入口码。code 唯一（409），role 限 admin|user|external_customer，tenant 须存在。"""
    if body.role not in _ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="role must be 'admin', 'user' or 'external_customer'",
        )
    async with async_session_factory() as session:
        tenant = await session.execute(
            select(TenantRow.id).where(TenantRow.id == body.tenant_id)
        )
        if tenant.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

        existing = await session.execute(
            select(EntryCodeRow.id).where(EntryCodeRow.code == body.code)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Entry code already exists"
            )

        row = EntryCodeRow(
            code=body.code,
            tenant_id=body.tenant_id,
            role=body.role,
            label=body.label,
            is_active=True,
            created_by=admin.id if admin is not None else None,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Entry code already exists"
            )
        await session.refresh(row)
        return _to_response(row)


@router.get("", response_model=list[EntryCodeResponse])
async def list_entry_codes():
    """列所有入口码。"""
    async with async_session_factory() as session:
        result = await session.execute(select(EntryCodeRow).order_by(EntryCodeRow.created_at))
        return [_to_response(c) for c in result.scalars().all()]


@router.patch("/{code_id}", response_model=EntryCodeResponse)
async def update_entry_code(code_id: uuid.UUID, body: EntryCodeUpdateRequest):
    """改码值 / 角色 / 描述 / 启停。"""
    if body.role is not None and body.role not in _ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="role must be 'admin', 'user' or 'external_customer'",
        )
    async with async_session_factory() as session:
        result = await session.execute(select(EntryCodeRow).where(EntryCodeRow.id == code_id))
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry code not found")

        if body.code is not None and body.code != row.code:
            dup = await session.execute(
                select(EntryCodeRow.id).where(EntryCodeRow.code == body.code)
            )
            if dup.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="Entry code already exists"
                )
            row.code = body.code
        if body.role is not None:
            row.role = body.role
        if body.label is not None:
            row.label = body.label
        if body.is_active is not None:
            row.is_active = body.is_active

        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Entry code already exists"
            )
        await session.refresh(row)
        return _to_response(row)


@router.delete("/{code_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry_code(code_id: uuid.UUID):
    """删入口码。已注册的用户不受影响（租户/角色已落在 UserRow 上）。"""
    async with async_session_factory() as session:
        result = await session.execute(select(EntryCodeRow).where(EntryCodeRow.id == code_id))
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry code not found")
        await session.delete(row)
        await session.commit()
