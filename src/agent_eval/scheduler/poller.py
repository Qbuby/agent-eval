from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from agent_eval.data.trace_extractor import RunSummary, TraceExtractor
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import TraceWatchCursorRow

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 5


class TracePoller:
    def __init__(self, extractor: TraceExtractor):
        self._extractor = extractor

    async def poll_project(self, project_name: str) -> list[RunSummary]:
        cursor = await self._get_or_create_cursor(project_name)
        if cursor.status != "active":
            return []

        try:
            runs = await self._extractor.list_runs(
                project_name,
                start_time=cursor.last_seen_time,
                status=None,
                limit=100,
            )

            seen_ids = set(cursor.last_seen_run_ids or [])
            if seen_ids:
                runs = [r for r in runs if r.id not in seen_ids]

            if runs:
                latest_time = max(
                    (r.start_time for r in runs if r.start_time),
                    default=None,
                )
                runs_at_latest = [
                    r.id for r in runs
                    if r.start_time == latest_time
                ] if latest_time else []

                if latest_time and latest_time == cursor.last_seen_time:
                    new_seen_ids = list(seen_ids | set(runs_at_latest))
                else:
                    new_seen_ids = runs_at_latest

                await self._update_cursor(
                    project_name,
                    last_seen_run_id=runs_at_latest[0] if runs_at_latest else runs[-1].id,
                    last_seen_run_ids=new_seen_ids,
                    last_seen_time=latest_time,
                    runs_fetched=len(runs),
                )

            await self._mark_polled(project_name)
            return runs

        except Exception as e:
            logger.error("Poll failed for %s: %s", project_name, e)
            await self._record_error(project_name, str(e))
            return []

    async def _get_or_create_cursor(self, project_name: str) -> TraceWatchCursorRow:
        async with async_session_factory() as session:
            result = await session.execute(
                select(TraceWatchCursorRow).where(
                    TraceWatchCursorRow.project_name == project_name
                )
            )
            cursor = result.scalar_one_or_none()
            if cursor is None:
                cursor = TraceWatchCursorRow(project_name=project_name)
                session.add(cursor)
                await session.commit()
                await session.refresh(cursor)
            return cursor

    async def _update_cursor(
        self,
        project_name: str,
        *,
        last_seen_run_id: str,
        last_seen_run_ids: list[str],
        last_seen_time: datetime | None,
        runs_fetched: int,
    ) -> None:
        async with async_session_factory() as session:
            await session.execute(
                update(TraceWatchCursorRow)
                .where(TraceWatchCursorRow.project_name == project_name)
                .values(
                    last_seen_run_id=last_seen_run_id,
                    last_seen_run_ids=last_seen_run_ids,
                    last_seen_time=last_seen_time,
                    runs_fetched_total=TraceWatchCursorRow.runs_fetched_total + runs_fetched,
                    error_message=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    async def _mark_polled(self, project_name: str) -> None:
        async with async_session_factory() as session:
            await session.execute(
                update(TraceWatchCursorRow)
                .where(TraceWatchCursorRow.project_name == project_name)
                .values(last_poll_at=datetime.now(timezone.utc))
            )
            await session.commit()

    async def _record_error(self, project_name: str, error: str) -> None:
        async with async_session_factory() as session:
            result = await session.execute(
                select(TraceWatchCursorRow).where(
                    TraceWatchCursorRow.project_name == project_name
                )
            )
            cursor = result.scalar_one_or_none()
            if cursor is None:
                return

            consecutive = self._count_consecutive_from_message(cursor.error_message, error)
            values: dict = {
                "error_message": f"[{consecutive}] {error}",
                "updated_at": datetime.now(timezone.utc),
                "last_poll_at": datetime.now(timezone.utc),
            }
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                values["status"] = "error"
                logger.warning(
                    "Pausing project %s after %d consecutive failures",
                    project_name,
                    consecutive,
                )

            await session.execute(
                update(TraceWatchCursorRow)
                .where(TraceWatchCursorRow.project_name == project_name)
                .values(**values)
            )
            await session.commit()

    @staticmethod
    def _count_consecutive_from_message(existing_msg: str | None, new_error: str) -> int:
        if not existing_msg:
            return 1
        try:
            count_str = existing_msg.split("]")[0].lstrip("[")
            return int(count_str) + 1
        except (ValueError, IndexError):
            return 1
