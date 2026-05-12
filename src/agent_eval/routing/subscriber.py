from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from agent_eval.routing.engine import RoutingEngine
from agent_eval.scheduler.events import NewRunsEvent

logger = logging.getLogger(__name__)


class RoutingSubscriber:
    def __init__(self, engine: RoutingEngine):
        self._engine = engine
        self._background_tasks: set[asyncio.Task] = set()

    async def handle_new_runs(self, event: NewRunsEvent) -> None:
        runs_as_dicts = []
        for r in event.runs:
            run_dict: dict[str, Any] = {
                "id": r.id,
                "name": r.name,
                "status": r.status,
                "start_time": r.start_time,
                "tags": r.tags,
            }
            if r.start_time and r.latency_s is not None:
                run_dict["end_time"] = r.start_time + timedelta(seconds=r.latency_s)
            runs_as_dicts.append(run_dict)

        task = asyncio.create_task(
            self._process_async(event.project_name, runs_as_dicts),
            name=f"routing-{event.project_name}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Routing task failed: %s", task.exception())

    async def _process_async(self, project_name: str, runs: list[dict]) -> None:
        try:
            results = await self._engine.process_with_retry(project_name, runs)
            routed = sum(1 for r in results if r["status"] == "routed")
            failed = sum(1 for r in results if r["status"] == "failed")
            skipped = sum(1 for r in results if r["status"] == "skipped")
            logger.info(
                "Routing complete for %s: %d routed, %d failed, %d skipped",
                project_name, routed, failed, skipped,
            )
        except Exception as e:
            logger.error("Routing subscriber error for %s: %s", project_name, e)
