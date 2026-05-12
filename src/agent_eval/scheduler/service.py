from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select, update

from agent_eval.config_service import ConfigService, config_service
from agent_eval.data.trace_extractor import TraceExtractor
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import TraceWatchCursorRow
from agent_eval.scheduler.events import EventBus, EventHandlerError, NewRunsEvent
from agent_eval.scheduler.poller import TracePoller

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 60


class SchedulerService:
    def __init__(
        self,
        extractor: TraceExtractor | None = None,
        config: ConfigService | None = None,
    ):
        self._extractor = extractor or TraceExtractor()
        self._config = config or config_service
        self._poller = TracePoller(self._extractor)
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False
        self._poll_interval: int = DEFAULT_POLL_INTERVAL
        self.event_bus = EventBus()

        self._config.on_change(self._on_config_change)

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._poll_interval = await self._get_poll_interval()

        enabled = await self._config.get("scheduler.enabled")
        if enabled is False:
            logger.info("Scheduler disabled by config")
            self._running = False
            return

        cursors = await self._load_active_cursors()
        for project_name in cursors:
            self._start_watch_task(project_name)

        logger.info("Scheduler started with %d watches", len(self._tasks))

    async def stop(self) -> None:
        if not self._running:
            return

        self._running = False
        for task in self._tasks.values():
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        logger.info("Scheduler stopped")

    async def add_watch(self, project_name: str) -> None:
        if project_name in self._tasks:
            return

        async with async_session_factory() as session:
            result = await session.execute(
                select(TraceWatchCursorRow).where(
                    TraceWatchCursorRow.project_name == project_name
                )
            )
            cursor = result.scalar_one_or_none()
            if cursor is None:
                session.add(TraceWatchCursorRow(project_name=project_name, status="active"))
                await session.commit()
            elif cursor.status != "active":
                await session.execute(
                    update(TraceWatchCursorRow)
                    .where(TraceWatchCursorRow.project_name == project_name)
                    .values(status="active", error_message=None)
                )
                await session.commit()

        if self._running:
            self._start_watch_task(project_name)

    async def remove_watch(self, project_name: str) -> None:
        task = self._tasks.pop(project_name, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        async with async_session_factory() as session:
            await session.execute(
                update(TraceWatchCursorRow)
                .where(TraceWatchCursorRow.project_name == project_name)
                .values(status="paused")
            )
            await session.commit()

    async def pause_watch(self, project_name: str) -> None:
        task = self._tasks.pop(project_name, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        async with async_session_factory() as session:
            await session.execute(
                update(TraceWatchCursorRow)
                .where(TraceWatchCursorRow.project_name == project_name)
                .values(status="paused")
            )
            await session.commit()

    async def resume_watch(self, project_name: str) -> None:
        async with async_session_factory() as session:
            await session.execute(
                update(TraceWatchCursorRow)
                .where(TraceWatchCursorRow.project_name == project_name)
                .values(status="active", error_message=None)
            )
            await session.commit()

        if self._running and project_name not in self._tasks:
            self._start_watch_task(project_name)

    async def trigger_poll(self, project_name: str) -> list[dict]:
        runs = await self._poller.poll_project(project_name)
        if runs:
            try:
                await self.event_bus.publish(NewRunsEvent(project_name=project_name, runs=runs))
            except EventHandlerError:
                pass
        return [{"id": r.id, "name": r.name, "status": r.status} for r in runs]

    async def get_status(self) -> dict[str, Any]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(TraceWatchCursorRow).order_by(TraceWatchCursorRow.project_name)
            )
            cursors = result.scalars().all()

        watches = []
        for c in cursors:
            watches.append({
                "project_name": c.project_name,
                "status": c.status,
                "last_poll_at": c.last_poll_at.isoformat() if c.last_poll_at else None,
                "last_seen_time": c.last_seen_time.isoformat() if c.last_seen_time else None,
                "runs_fetched_total": c.runs_fetched_total,
                "error_message": c.error_message,
                "is_polling": c.project_name in self._tasks,
            })

        return {
            "running": self._running,
            "poll_interval_seconds": self._poll_interval,
            "watches": watches,
        }

    def _start_watch_task(self, project_name: str) -> None:
        task = asyncio.create_task(
            self._poll_loop(project_name), name=f"poll-{project_name}"
        )
        self._tasks[project_name] = task

    async def _poll_loop(self, project_name: str) -> None:
        try:
            while self._running:
                runs = await self._poller.poll_project(project_name)
                if runs:
                    try:
                        await self.event_bus.publish(
                            NewRunsEvent(project_name=project_name, runs=runs)
                        )
                    except EventHandlerError:
                        pass
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Poll loop crashed for %s: %s", project_name, e)
        finally:
            self._tasks.pop(project_name, None)

    async def _get_poll_interval(self) -> int:
        val = await self._config.get("scheduler.poll_interval_seconds")
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
        return DEFAULT_POLL_INTERVAL

    def _on_config_change(self, key: str, value: Any) -> None:
        if key == "scheduler.poll_interval_seconds":
            try:
                self._poll_interval = int(value)
                logger.info("Poll interval updated to %d seconds", self._poll_interval)
            except (TypeError, ValueError):
                pass
        elif key == "scheduler.enabled":
            if value is False and self._running:
                asyncio.create_task(self.stop())
            elif value is True and not self._running:
                asyncio.create_task(self.start())

    async def _load_active_cursors(self) -> list[str]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(TraceWatchCursorRow.project_name).where(
                    TraceWatchCursorRow.status == "active"
                )
            )
            return list(result.scalars().all())
