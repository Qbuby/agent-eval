"""Background warmer for the traces list_runs cache.

LangSmith list_runs latency is highly variable (12-130s observed for the
same query). The user lands on /traces and immediately hits Query — we want
that path to be a cache hit, not a fresh upstream call. So we keep a small
list of recently-queried projects (LRU, populated each time list_runs is
called) and a long-running task in the lifespan reissues list_runs for them
on a fixed cadence.

Defaults: 8 projects, every 45s, with the same shape the UI sends
(limit=50, status=success, no I/O, no model enrichment). The cache TTL is
60s, so this stays comfortably ahead of expiry.
"""
from __future__ import annotations

import asyncio
import logging
import time

from agent_eval.data.trace_extractor import (
    TraceExtractor, get_recent_projects,
)

logger = logging.getLogger(__name__)


WARM_INTERVAL_S = 45
WARM_LIMIT = 50
WARM_STATUS = "success"


class TracesWarmer:

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._extractor: TraceExtractor | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        # Build extractor lazily so a missing LANGSMITH_API_KEY doesn't crash
        # app startup — the warmer just becomes a no-op in that case.
        try:
            self._extractor = TraceExtractor()
        except Exception as e:
            logger.warning("traces warmer: failed to init extractor (%s); disabling", e)
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="traces-warmer")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        # Stagger the first run so we don't pile on top of any other startup
        # work that hits LangSmith (e.g. scheduler poll).
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=10.0)
            return  # stopped before first iteration
        except asyncio.TimeoutError:
            pass

        while not self._stop.is_set():
            projects = get_recent_projects()
            if projects and self._extractor is not None:
                for name in projects:
                    if self._stop.is_set():
                        break
                    try:
                        t0 = time.monotonic()
                        # Warm with the same shape the UI sends — preview
                        # column requires with_io=True for projects whose
                        # inputs_preview/outputs_preview fields are empty
                        # (which is most of them in practice).
                        await self._extractor.list_runs(
                            name,
                            status=WARM_STATUS,
                            limit=WARM_LIMIT,
                            enrich_models=False,
                            with_io=True,
                        )
                        logger.debug(
                            "traces warmer: refreshed %s in %.2fs",
                            name, time.monotonic() - t0,
                        )
                    except Exception as e:
                        # Never let a single project's failure kill the loop —
                        # LangSmith outages, auth issues, deleted projects all
                        # land here and should just be logged.
                        logger.warning(
                            "traces warmer: refresh failed for %s: %s", name, e,
                        )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=WARM_INTERVAL_S)
                return
            except asyncio.TimeoutError:
                continue


_warmer: TracesWarmer | None = None


def get_warmer() -> TracesWarmer:
    global _warmer
    if _warmer is None:
        _warmer = TracesWarmer()
    return _warmer
