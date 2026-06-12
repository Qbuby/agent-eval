"""Admin 开户 / 用户管理端点（§6.1）。

全部挂 ``require_role(ROLE_ADMIN)`` —— 仅内部 admin 可建租户、开外部客户账号、
管用户。查询用 ``async with async_session_factory()`` 直连（不依赖 get_db）。

为何 admin 能跨租户读 users/tenants：
- ``users`` / ``tenants`` 表本身不挂 TenantMixin，监听器不对它们注入过滤；
- 且 admin 登录态是 superadmin，监听器对其所有读查询整体旁路。
所以这里查 UserRow / TenantRow 天然全租户可见，无需手写 .where(tenant_id)。
按 tenant 过滤是显式可选项（GET /users?tenant_id=）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from agent_eval.auth.dependencies import ROLE_ADMIN, ROLE_EXTERNAL, ROLE_USER, require_role
from agent_eval.auth.security import hash_password
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import TenantRow, UserRow

router = APIRouter(
    prefix="/api/admin",
    tags=["admin-tenants"],
    dependencies=[Depends(require_role(ROLE_ADMIN))],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TenantCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    slug: str = Field(..., min_length=1, max_length=64)


class TenantUpdateRequest(BaseModel):
    """改名 / 启停。status 取 'active' | 'disabled'。"""

    name: str | None = Field(None, min_length=1, max_length=128)
    status: str | None = Field(None, max_length=16)


class TenantResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    user_count: int = 0


class TenantUserCreateRequest(BaseModel):
    """在某租户下开外部客户账号。"""

    username: str = Field(..., min_length=1, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    role: str
    is_active: bool
    tenant_id: uuid.UUID
    is_superadmin: bool
    created_at: datetime
    updated_at: datetime


class UserUpdateRequest(BaseModel):
    """启停 / 改角色 / 重置密码。"""

    is_active: bool | None = None
    role: str | None = Field(None, max_length=16)
    password: str | None = Field(None, min_length=6, max_length=128)


def _tenant_to_response(t: TenantRow, user_count: int = 0) -> TenantResponse:
    return TenantResponse(
        id=t.id,
        name=t.name,
        slug=t.slug,
        status=t.status,
        created_by=t.created_by,
        created_at=t.created_at,
        updated_at=t.updated_at,
        user_count=user_count,
    )


def _user_to_response(u: UserRow) -> UserResponse:
    return UserResponse(
        id=u.id,
        username=u.username,
        email=u.email,
        role=u.role,
        is_active=u.is_active,
        tenant_id=u.tenant_id,
        is_superadmin=u.is_superadmin,
        created_at=u.created_at,
        updated_at=u.updated_at,
    )


# ---------------------------------------------------------------------------
# 租户
# ---------------------------------------------------------------------------


@router.post("/tenants", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreateRequest,
    admin: UserRow = Depends(require_role(ROLE_ADMIN)),
):
    """建租户。slug 唯一，冲突回 409。"""
    async with async_session_factory() as session:
        existing = await session.execute(
            select(TenantRow.id).where(TenantRow.slug == body.slug)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tenant slug already exists",
            )

        tenant = TenantRow(
            name=body.name,
            slug=body.slug,
            status="active",
            created_by=admin.id if admin is not None else None,
        )
        session.add(tenant)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tenant slug already exists",
            )
        await session.refresh(tenant)
        return _tenant_to_response(tenant, user_count=0)


@router.get("/tenants", response_model=list[TenantResponse])
async def list_tenants():
    """列租户，含每个租户的用户数。"""
    async with async_session_factory() as session:
        # 一次性取所有租户的用户数：tenant_id -> count
        count_rows = await session.execute(
            select(UserRow.tenant_id, func.count(UserRow.id)).group_by(UserRow.tenant_id)
        )
        counts = {tid: cnt for tid, cnt in count_rows.all()}

        result = await session.execute(select(TenantRow).order_by(TenantRow.created_at))
        tenants = result.scalars().all()
        return [_tenant_to_response(t, user_count=counts.get(t.id, 0)) for t in tenants]


@router.patch("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(tenant_id: uuid.UUID, body: TenantUpdateRequest):
    """改名 / 启停（status）。"""
    async with async_session_factory() as session:
        result = await session.execute(select(TenantRow).where(TenantRow.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
            )

        if body.name is not None:
            tenant.name = body.name
        if body.status is not None:
            if body.status not in ("active", "disabled"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="status must be 'active' or 'disabled'",
                )
            tenant.status = body.status

        tenant.updated_at = datetime.now(timezone.utc)
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)

        cnt = await session.execute(
            select(func.count(UserRow.id)).where(UserRow.tenant_id == tenant.id)
        )
        return _tenant_to_response(tenant, user_count=cnt.scalar_one())


@router.post(
    "/tenants/{tenant_id}/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant_user(tenant_id: uuid.UUID, body: TenantUserCreateRequest):
    """在某租户下开外部客户账号（role=external_customer, is_superadmin=False）。"""
    async with async_session_factory() as session:
        tenant = await session.execute(select(TenantRow.id).where(TenantRow.id == tenant_id))
        if tenant.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
            )

        existing = await session.execute(
            select(UserRow.id).where(
                (UserRow.username == body.username) | (UserRow.email == body.email)
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username or email already registered",
            )

        user = UserRow(
            username=body.username,
            email=body.email,
            hashed_password=hash_password(body.password),
            role=ROLE_EXTERNAL,
            tenant_id=tenant_id,
            is_superadmin=False,
            is_active=True,
        )
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username or email already registered",
            )
        await session.refresh(user)
        return _user_to_response(user)


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    tenant_id: uuid.UUID | None = Query(None, description="按租户过滤"),
):
    """列用户。可选按 tenant_id 过滤（users 表不被监听器自动过滤，需显式 where）。"""
    async with async_session_factory() as session:
        stmt = select(UserRow).order_by(UserRow.created_at)
        if tenant_id is not None:
            stmt = stmt.where(UserRow.tenant_id == tenant_id)
        result = await session.execute(stmt)
        return [_user_to_response(u) for u in result.scalars().all()]


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdateRequest,
    admin: UserRow = Depends(require_role(ROLE_ADMIN)),
):
    """启停 / 改角色 / 重置密码。

    两道自锁防护：
    1. 不能改自己的启停状态或角色（admin 把自己停用/降级会直接锁死登录；改密码允许）。
    2. 不能停用或降级「最后一个活跃 admin」（否则系统再无人可管理）。
    """
    async with async_session_factory() as session:
        result = await session.execute(select(UserRow).where(UserRow.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )

        # 这两个操作会改变「该用户是否为活跃 admin」，是自锁/锁死系统的风险点。
        deactivating = body.is_active is False and user.is_active
        demoting = body.role is not None and body.role != ROLE_ADMIN and user.role == ROLE_ADMIN

        # 防护 1：禁止改自己的启停 / 角色（admin 在登录态下 admin 不为 None）。
        if admin is not None and admin.id == user.id:
            if body.is_active is not None and body.is_active != user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="不能修改自己的启用状态",
                )
            if body.role is not None and body.role != user.role:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="不能修改自己的角色",
                )

        # 防护 2：禁止停用或降级最后一个活跃 admin。
        if deactivating or demoting:
            active_admins = await session.execute(
                select(func.count(UserRow.id)).where(
                    UserRow.role == ROLE_ADMIN, UserRow.is_active == True  # noqa: E712
                )
            )
            if active_admins.scalar_one() <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="不能停用或降级最后一个活跃管理员",
                )

        if body.is_active is not None:
            user.is_active = body.is_active
        if body.role is not None:
            if body.role not in (ROLE_ADMIN, ROLE_USER, ROLE_EXTERNAL):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="role must be 'admin', 'user' or 'external_customer'",
                )
            user.role = body.role
        if body.password is not None:
            user.hashed_password = hash_password(body.password)

        user.updated_at = datetime.now(timezone.utc)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return _user_to_response(user)
