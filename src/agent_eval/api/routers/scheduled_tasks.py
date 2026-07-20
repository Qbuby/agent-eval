"""定时评估任务的 HTTP CRUD + 启停 + 立即执行。

``scheduled_eval_tasks`` 表的管理面板 API：Web 前端与飞书机器人（走本地 HTTP
自调，见 feishu/tools.py）共用这一套端点。挂 ``require_internal`` 门禁 + 租户
上下文注入（继承 TenantMixin，读写自动按当前租户隔离）；删除类要 admin。

任务的 ``spec`` 存一份等价 ``StartEvalRequest`` 的 dump，创建/更新时用
``StartEvalRequest(**spec)`` 预校验（早失败早报错，避免坏 spec 到调度器才炸）。
``next_run_at`` 由 ``compute_next_run`` 依 ``schedule`` 首次计算；启用/改 schedule
时重算。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent_eval.api.schemas import StartEvalRequest
from agent_eval.auth.dependencies import ROLE_ADMIN, require_internal, require_role
from agent_eval.db import async_session_factory
from agent_eval.db_models.repository import Repository  # noqa: F401  (parity w/ other routers)
from agent_eval.db_models.tables import ScheduledEvalTaskRow
from agent_eval.scheduler.eval_scheduler import compute_next_run

router = APIRouter(
    prefix="/api/scheduled-tasks",
    tags=["scheduled-tasks"],
    dependencies=[Depends(require_internal())],
)


# ── 请求/响应模型 ──────────────────────────────────────────────────────

class CreateScheduledTaskRequest(BaseModel):
    name: str
    # 等价 StartEvalRequest 的 dump（样例来源四选一 + agent + evaluator_ids + ...）。
    spec: dict[str, Any]
    # {"kind": "interval", "seconds": int} | {"kind": "daily", "at": "HH:MM"}
    schedule: dict[str, Any]
    notify_open_ids: list[str] = Field(default_factory=list)
    enabled: bool = True
    created_by: str | None = None  # 建任务的飞书 open_id（机器人自调时带上，可空）


class UpdateScheduledTaskRequest(BaseModel):
    name: str | None = None
    spec: dict[str, Any] | None = None
    schedule: dict[str, Any] | None = None
    notify_open_ids: list[str] | None = None
    enabled: bool | None = None


def _validate_spec(spec: dict[str, Any]) -> None:
    """用 StartEvalRequest 预校验 spec；不合法抛 400。"""
    try:
        StartEvalRequest(**spec)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"spec 不是合法的评估请求：{e}") from e


def _validate_schedule(schedule: dict[str, Any]) -> datetime:
    """校验 schedule 并返回首个 next_run_at；不合法抛 400。"""
    nxt = compute_next_run(schedule)
    if nxt is None:
        raise HTTPException(
            status_code=400,
            detail='schedule 不合法。示例：{"kind":"interval","seconds":3600} '
            '或 {"kind":"daily","at":"09:00"}',
        )
    return nxt


def _to_dict(t: ScheduledEvalTaskRow) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "name": t.name,
        "spec": t.spec,
        "schedule": t.schedule,
        "notify_open_ids": t.notify_open_ids or [],
        "enabled": t.enabled,
        "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
        "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
        "last_run_id": t.last_run_id,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


async def _get_or_404(session, task_id: str) -> ScheduledEvalTaskRow:
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid task id")
    row = (await session.execute(
        select(ScheduledEvalTaskRow).where(ScheduledEvalTaskRow.id == tid)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="scheduled task not found")
    return row


# ── 端点 ────────────────────────────────────────────────────────────────

@router.get("")
async def list_tasks():
    """列出当前租户的定时评估任务（按创建时间倒序）。"""
    async with async_session_factory() as session:
        rows = (await session.execute(
            select(ScheduledEvalTaskRow).order_by(ScheduledEvalTaskRow.created_at.desc())
        )).scalars().all()
    return {"tasks": [_to_dict(r) for r in rows]}


@router.get("/{task_id}")
async def get_task(task_id: str):
    async with async_session_factory() as session:
        row = await _get_or_404(session, task_id)
        return _to_dict(row)


@router.post("")
async def create_task(req: CreateScheduledTaskRequest):
    """新建定时任务。校验 spec + schedule，落库并计算首个 next_run_at。"""
    _validate_spec(req.spec)
    nxt = _validate_schedule(req.schedule)
    async with async_session_factory() as session:
        row = ScheduledEvalTaskRow(
            name=req.name,
            spec=req.spec,
            schedule=req.schedule,
            notify_open_ids=req.notify_open_ids,
            enabled=req.enabled,
            next_run_at=nxt if req.enabled else None,
            created_by=req.created_by,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_dict(row)


@router.put("/{task_id}")
async def update_task(task_id: str, req: UpdateScheduledTaskRequest):
    """更新定时任务。改 spec/schedule 会重校验；改 schedule 或重新启用时重算
    next_run_at。"""
    async with async_session_factory() as session:
        row = await _get_or_404(session, task_id)

        if req.spec is not None:
            _validate_spec(req.spec)
            row.spec = req.spec
        if req.name is not None:
            row.name = req.name
        if req.notify_open_ids is not None:
            row.notify_open_ids = req.notify_open_ids

        schedule_changed = req.schedule is not None
        if schedule_changed:
            _validate_schedule(req.schedule)
            row.schedule = req.schedule

        if req.enabled is not None:
            row.enabled = req.enabled

        # 重算 next_run_at：改了 schedule，或从停用转启用。
        if row.enabled and (schedule_changed or req.enabled is True):
            row.next_run_at = compute_next_run(row.schedule)
        elif req.enabled is False:
            row.next_run_at = None

        await session.commit()
        await session.refresh(row)
        return _to_dict(row)


@router.delete("/{task_id}", dependencies=[Depends(require_role(ROLE_ADMIN))])
async def delete_task(task_id: str):
    """删除定时任务（硬删，不可逆，需 admin）。"""
    async with async_session_factory() as session:
        row = await _get_or_404(session, task_id)
        await session.delete(row)
        await session.commit()
    return {"ok": True, "deleted": task_id}


@router.post("/{task_id}/pause")
async def pause_task(task_id: str):
    """暂停任务：enabled=False，清空 next_run_at（调度器不再扫到）。"""
    async with async_session_factory() as session:
        row = await _get_or_404(session, task_id)
        row.enabled = False
        row.next_run_at = None
        await session.commit()
        return _to_dict(row)


@router.post("/{task_id}/resume")
async def resume_task(task_id: str):
    """恢复任务：enabled=True，按 schedule 重算 next_run_at。"""
    async with async_session_factory() as session:
        row = await _get_or_404(session, task_id)
        nxt = compute_next_run(row.schedule)
        if nxt is None:
            raise HTTPException(status_code=400, detail="任务 schedule 不合法，无法恢复")
        row.enabled = True
        row.next_run_at = nxt
        await session.commit()
        return _to_dict(row)


@router.post("/{task_id}/run-now")
async def run_now(task_id: str, user=Depends(require_internal())):
    """立即执行一次该任务的评估（不影响其定时节奏）。

    走与调度器完全相同的解析链路 + start_run，通知目标用任务配置的
    notify_open_ids。当前用户上下文即租户上下文（HTTP 依赖已注入），故这里
    无需手动 set_tenant_context。
    """
    from agent_eval.api.routers.evaluation import resolve_eval_start_args
    from agent_eval.evaluation.langfuse_runner import start_run

    async with async_session_factory() as session:
        repo = Repository(session)
        row = await _get_or_404(session, task_id)
        spec = dict(row.spec or {})
        notify_open_ids = list(row.notify_open_ids or [])
        try:
            req = StartEvalRequest(**spec)
            args = await resolve_eval_start_args(req, session, repo)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"任务 spec 解析失败：{e}") from e

        run_id = await start_run(**args, notify_open_ids=notify_open_ids)

        row.last_run_at = datetime.now(timezone.utc)
        row.last_run_id = run_id
        await session.commit()

    return {"ok": True, "run_id": run_id, "task_id": task_id}
