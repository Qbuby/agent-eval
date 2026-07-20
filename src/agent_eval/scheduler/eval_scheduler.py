"""定时评估调度器：按 interval / 每日定点自动发起评估 run。

与既有 ``scheduler/service.py::SchedulerService``（轮询 Langfuse traces 摄取
新 run）**语义不同、刻意分开**：本调度器读 ``scheduled_eval_tasks`` 表，到点
用与 HTTP ``/runs/start`` 完全相同的解析链路（``resolve_eval_start_args``）+
``start_run`` 发起一次评估，并把任务配置的通知目标透传给完成通知。

生命周期照 SchedulerService / FeishuBotService 范式：``start()`` 拉起后台
单循环，``stop()`` 收尾，由 FastAPI lifespan 调度。

租户上下文：调度器在常驻进程里跑（无 HTTP 请求上下文），发起评估前必须按
任务所属租户 ``set_tenant_context``，否则 ``start_run`` 建的 ``test_runs`` 行
会落到内部 sentinel 租户而非任务真正的租户。每个任务处理完 try/finally
reset，避免 ContextVar 泄漏到下一个任务。

单实例假设：循环无分布式锁，多副本部署会重复触发。当前单容器部署，暂不做
分布式锁；若将来横向扩展需加（如 SELECT ... FOR UPDATE SKIP LOCKED 抢占）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import ScheduledEvalTaskRow
from agent_eval.db_models.tenant_context import (
    TenantContext,
    reset_tenant_context,
    set_tenant_context,
)

logger = logging.getLogger(__name__)

# 扫描周期：每 30s 查一次 due 任务。定时评估的时间粒度到分钟即可，30s 足够。
_SCAN_INTERVAL = 30


def compute_next_run(schedule: dict[str, Any], *, now: datetime | None = None) -> datetime | None:
    """按 schedule 算下一次触发时刻（UTC，纯函数，可离线单测）。

    支持两种：
    - ``{"kind": "interval", "seconds": N}`` → now + N 秒。
    - ``{"kind": "daily", "at": "HH:MM"}`` → 今天该时刻；已过则明天该时刻。
      ``at`` 按 UTC 解释（与库内 datetime 统一，避免时区歧义；调用方若需本地
      时间应在展示层转换）。

    无法识别的 schedule 返回 None（调用方据此禁用或标错，不崩）。
    """
    now = now or datetime.now(timezone.utc)
    kind = (schedule or {}).get("kind")

    if kind == "interval":
        try:
            seconds = int(schedule.get("seconds") or 0)
        except (TypeError, ValueError):
            return None
        if seconds <= 0:
            return None
        return now + timedelta(seconds=seconds)

    if kind == "daily":
        at = str(schedule.get("at") or "").strip()
        try:
            hh, mm = at.split(":")
            hour, minute = int(hh), int(mm)
        except (ValueError, AttributeError):
            return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate = candidate + timedelta(days=1)
        return candidate

    return None


class EvalScheduler:
    """定时评估任务的后台循环持有者。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="eval-scheduler")
        logger.info("eval scheduler started (scan every %ds)", _SCAN_INTERVAL)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        logger.info("eval scheduler stopped")

    async def _loop(self) -> None:
        try:
            while self._running:
                try:
                    await self._tick()
                except Exception as e:  # noqa: BLE001
                    logger.exception("eval scheduler tick failed: %s", e)
                await asyncio.sleep(_SCAN_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        """扫一遍 due 任务并逐个触发。

        跨租户扫描：以「系统上下文」（无租户过滤）读全部 due 任务，再在**每个
        任务自己的租户上下文**里发起评估。故这里的 SELECT 不设租户上下文
        （None = superadmin 旁路，见 tenant_context 文档）。
        """
        now = datetime.now(timezone.utc)
        async with async_session_factory() as session:
            due = (await session.execute(
                select(ScheduledEvalTaskRow).where(
                    ScheduledEvalTaskRow.enabled.is_(True),
                    ScheduledEvalTaskRow.next_run_at.isnot(None),
                    ScheduledEvalTaskRow.next_run_at <= now,
                )
            )).scalars().all()

        for task in due:
            await self._trigger_task(task.id)

    async def _trigger_task(self, task_id: Any) -> None:
        """在任务所属租户上下文里发起一次评估，并推进 next_run_at。

        每个任务独立 session + 独立租户上下文（try/finally reset），单个任务
        失败不影响其它任务。发起失败时仍推进 next_run_at（避免坏任务卡在过去
        时刻被每轮反复重试），并把错误记入日志。
        """
        from agent_eval.api.schemas import StartEvalRequest
        from agent_eval.db_models.repository import Repository
        from agent_eval.evaluation.langfuse_runner import start_run
        from agent_eval.api.routers.evaluation import resolve_eval_start_args

        token = None
        async with async_session_factory() as session:
            repo = Repository(session)
            # 重新读一遍（避免用扫描期的陈旧行），并二次确认仍 due + enabled。
            task = (await session.execute(
                select(ScheduledEvalTaskRow).where(ScheduledEvalTaskRow.id == task_id)
            )).scalar_one_or_none()
            if task is None or not task.enabled:
                return

            tenant_id = task.tenant_id
            spec = dict(task.spec or {})
            notify_open_ids = list(task.notify_open_ids or [])
            schedule = dict(task.schedule or {})
            run_id: str | None = None
            err: str | None = None

            # 任务所属租户上下文（非 superadmin：只操作该租户数据）。
            token = set_tenant_context(TenantContext(tenant_id=tenant_id, superadmin=False))
            try:
                req = StartEvalRequest(**spec)
                args = await resolve_eval_start_args(req, session, repo)
                run_id = await start_run(**args, notify_open_ids=notify_open_ids)
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                logger.exception("scheduled task %s trigger failed: %s", task_id, err)
            finally:
                if token is not None:
                    reset_tenant_context(token)

            # 推进调度状态（无论成功失败都推进 next_run_at，避免坏任务卡死循环）。
            now = datetime.now(timezone.utc)
            task.last_run_at = now
            if run_id:
                task.last_run_id = run_id
            nxt = compute_next_run(schedule, now=now)
            if nxt is None:
                # schedule 非法：禁用任务，避免每轮空转。
                task.enabled = False
                logger.warning(
                    "scheduled task %s has invalid schedule %s; disabling",
                    task_id, schedule,
                )
            else:
                task.next_run_at = nxt
            await session.commit()

        if run_id:
            logger.info("scheduled task %s started run %s", task_id, run_id)


_scheduler: EvalScheduler | None = None


def get_eval_scheduler() -> EvalScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = EvalScheduler()
    return _scheduler
