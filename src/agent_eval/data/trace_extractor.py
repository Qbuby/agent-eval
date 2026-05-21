from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from langsmith import Client

from agent_eval.data._utils import normalize_messages, to_thread, truncate
from agent_eval.models.test_case import TestCase, ToolCallExpectation

logger = logging.getLogger(__name__)


_DETAIL_CACHE_MAX = 256
_DETAIL_CACHE_TTL_S = 300
_detail_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_detail_cache_lock = asyncio.Lock()

# Short-TTL list_runs cache keyed by query params. Helps dedupe the burst of
# calls the UI tends to make (query, pagination, repeat query without changes).
# TTL is sized for the background warmer (interval 45s) — we want a fresh
# warm-up to land before the previous entry expires, so each user click is
# always cache-served.
_LIST_RUNS_CACHE_MAX = 64
_LIST_RUNS_CACHE_TTL_S = 90
_list_runs_cache: "OrderedDict[str, tuple[float, list]]" = OrderedDict()

# Per-(project, root_id) model_name cache. Immutable history → long TTL is safe.
_MODEL_NAME_CACHE_MAX = 4096
_MODEL_NAME_CACHE_TTL_S = 3600
_model_name_cache: "OrderedDict[str, tuple[float, str]]" = OrderedDict()

# Per-(project, root_id) first-tool-call latency (seconds from root start).
# Sentinel: cache stores -1.0 when the root is known to make no tool calls
# (positive cache of "no tool"); any non-negative float means "tool found".
_FIRST_TOOL_CACHE_MAX = 4096
_FIRST_TOOL_CACHE_TTL_S = 3600
_first_tool_cache: "OrderedDict[str, tuple[float, float]]" = OrderedDict()


# Recently-queried project names for background warm-up. Keeps an LRU of up
# to 8 projects; the warmer task re-runs list_runs for them every minute so
# the user never hits a cold path on the common case.
_RECENT_PROJECTS_MAX = 8
_recent_projects: "OrderedDict[str, float]" = OrderedDict()


def _track_project_query(project_name: str) -> None:
    if not project_name:
        return
    _recent_projects[project_name] = time.monotonic()
    _recent_projects.move_to_end(project_name)
    while len(_recent_projects) > _RECENT_PROJECTS_MAX:
        _recent_projects.popitem(last=False)


def get_recent_projects() -> list[str]:
    """Snapshot of recently-queried project names (most-recent last)."""
    return list(_recent_projects.keys())


def _list_runs_cache_get(key: str) -> list | None:
    entry = _list_runs_cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _LIST_RUNS_CACHE_TTL_S:
        _list_runs_cache.pop(key, None)
        return None
    _list_runs_cache.move_to_end(key)
    return value


def _list_runs_cache_set(key: str, value: list) -> None:
    _list_runs_cache[key] = (time.monotonic(), value)
    _list_runs_cache.move_to_end(key)
    while len(_list_runs_cache) > _LIST_RUNS_CACHE_MAX:
        _list_runs_cache.popitem(last=False)


def _model_name_cache_get(key: str) -> str | None:
    entry = _model_name_cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _MODEL_NAME_CACHE_TTL_S:
        _model_name_cache.pop(key, None)
        return None
    _model_name_cache.move_to_end(key)
    return value


def _model_name_cache_set(key: str, value: str) -> None:
    _model_name_cache[key] = (time.monotonic(), value)
    _model_name_cache.move_to_end(key)
    while len(_model_name_cache) > _MODEL_NAME_CACHE_MAX:
        _model_name_cache.popitem(last=False)


def _first_tool_cache_get(key: str) -> float | None:
    entry = _first_tool_cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _FIRST_TOOL_CACHE_TTL_S:
        _first_tool_cache.pop(key, None)
        return None
    _first_tool_cache.move_to_end(key)
    return value


def _first_tool_cache_set(key: str, value: float) -> None:
    _first_tool_cache[key] = (time.monotonic(), value)
    _first_tool_cache.move_to_end(key)
    while len(_first_tool_cache) > _FIRST_TOOL_CACHE_MAX:
        _first_tool_cache.popitem(last=False)


def _cache_get(key: str) -> dict | None:
    entry = _detail_cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _DETAIL_CACHE_TTL_S:
        _detail_cache.pop(key, None)
        return None
    _detail_cache.move_to_end(key)
    return value


def _cache_set(key: str, value: dict) -> None:
    _detail_cache[key] = (time.monotonic(), value)
    _detail_cache.move_to_end(key)
    while len(_detail_cache) > _DETAIL_CACHE_MAX:
        _detail_cache.popitem(last=False)


@dataclass
class RunSummary:
    id: str
    name: str
    status: str
    start_time: datetime | None
    latency_s: float | None
    total_tokens: int | None
    error: str | None
    tags: list[str] = field(default_factory=list)
    input_preview: str = ""
    output_preview: str = ""
    model_name: str = ""
    first_token_s: float | None = None  # Time-to-first-token (seconds from run start)
    first_tool_call_s: float | None = None  # Seconds from run start to the first tool child's start (None if not yet resolved; see fill endpoint)


def _compute_ttft(run: Any) -> float | None:
    """Return seconds from run.start_time to run.first_token_time, or None.

    LangSmith returns start_time with tzinfo but first_token_time as naive UTC,
    so we normalize both to naive UTC before subtracting.
    """
    start = getattr(run, "start_time", None)
    ttft = getattr(run, "first_token_time", None)
    if start is None or ttft is None:
        return None
    try:
        s = start.replace(tzinfo=None) if start.tzinfo else start
        t = ttft.replace(tzinfo=None) if ttft.tzinfo else ttft
        delta = (t - s).total_seconds()
        return delta if delta >= 0 else None
    except Exception:
        return None


class TraceExtractor:

    def __init__(self, client: Client | None = None, **client_kwargs: Any):
        self.client = client or Client(**client_kwargs)

    async def list_runs(
        self,
        project_name: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        status: str | None = "success",
        tags: list[str] | None = None,
        limit: int = 100,
        enrich_models: bool = False,
        with_io: bool = False,
    ) -> list[RunSummary]:
        # Track the project for background warm-up.
        _track_project_query(project_name)
        # Cache key captures everything that affects the query result.
        # `enrich_models` and `with_io` are part of the key so a cheap entry
        # (model-less / preview-less) doesn't satisfy a richer follow-up call.
        cache_key = (
            f"{project_name}|{start_time.isoformat() if start_time else ''}"
            f"|{end_time.isoformat() if end_time else ''}|{status or ''}"
            f"|{','.join(sorted(tags or []))}|{limit}"
            f"|enrich={int(enrich_models)}|io={int(with_io)}"
        )
        cached = _list_runs_cache_get(cache_key)
        if cached is not None:
            return cached

        kwargs: dict[str, Any] = {
            "project_name": project_name,
            "is_root": True,
            "limit": limit,
        }
        # Field selection cuts LangSmith response time dramatically — full
        # inputs/outputs serialization is the dominant cost. We use
        # inputs_preview/outputs_preview (server-side truncated) instead of
        # full I/O for the list view; the details modal pulls everything via
        # /run_detail. `latency` is computed client-side from start/end_time
        # (it's not a selectable field on LangSmith's side).
        select_fields = [
            "id", "name", "status", "start_time", "end_time",
            "total_tokens", "error", "tags", "first_token_time", "trace_id",
        ]
        if with_io:
            select_fields += ["inputs", "outputs"]
        else:
            select_fields += ["inputs_preview", "outputs_preview"]
        kwargs["select"] = select_fields
        if start_time:
            kwargs["start_time"] = start_time
        if end_time:
            kwargs["end_time"] = end_time

        filters: list[str] = []
        if status:
            filters.append(f'eq(status, "{status}")')
        if tags:
            for tag in tags:
                filters.append(f'has(tags, "{tag}")')
        if filters:
            kwargs["filter"] = " and ".join(filters) if len(filters) > 1 else filters[0]

        runs = await to_thread(self.client.list_runs, **kwargs)
        root_runs = list(runs)

        # Model-name enrichment is opt-in. The window query against LangSmith
        # roughly doubles cold-path latency (50-80s extra), and the UI already
        # has a dedicated "补齐信息" button (→ /fill_models) for it. So the
        # default cold path returns model_name="" and lets the user trigger
        # enrichment explicitly. Already-cached model names are still surfaced
        # below regardless of this flag.
        model_map: dict[str, str] = {}
        if enrich_models:
            model_map = await self._build_model_map_via_window(project_name, root_runs)

        summaries = []
        for run in root_runs:
            # When with_io=False the LangSmith response carries inputs_preview
            # / outputs_preview (server-side truncated) — use those directly
            # to avoid pulling the full payload. With with_io=True we truncate
            # the real payload client-side.
            if with_io:
                input_preview = truncate(str(run.inputs or {}), 120)
                output_preview = truncate(str(run.outputs or {}), 120)
            else:
                ip = getattr(run, "inputs_preview", None)
                op = getattr(run, "outputs_preview", None)
                input_preview = truncate(str(ip), 120) if ip else ""
                output_preview = truncate(str(op), 120) if op else ""
            # `latency` is not a selectable field — compute it locally from
            # start/end_time (which is what the langsmith client property
            # would do anyway, but only when both are present).
            st = getattr(run, "start_time", None)
            et = getattr(run, "end_time", None)
            if st and et:
                try:
                    a = st.replace(tzinfo=None) if st.tzinfo else st
                    b = et.replace(tzinfo=None) if et.tzinfo else et
                    latency_s: float | None = (b - a).total_seconds()
                except Exception:
                    latency_s = None
            else:
                latency_s = None
            # Use enriched value if present; otherwise fall back to whatever
            # the model-name cache already has from a previous /fill_models or
            # /runs?enrich_models=true call. Empty string = unknown.
            model_name = model_map.get(str(run.id)) or (
                _model_name_cache_get(f"{project_name}:{run.id}") or ""
            )
            # first_tool_call_s is populated later via the enrich endpoint;
            # surface whatever is already cached so subsequent list_runs after
            # an enrich call can show the tool-call column without another rpc.
            ft_cached = _first_tool_cache_get(f"{project_name}:{run.id}")
            first_tool_call_s: float | None
            if ft_cached is None or ft_cached < 0:
                first_tool_call_s = None
            else:
                first_tool_call_s = ft_cached
            summaries.append(
                RunSummary(
                    id=str(run.id),
                    name=run.name or "",
                    status=run.status or "unknown",
                    start_time=run.start_time,
                    latency_s=latency_s,
                    total_tokens=run.total_tokens,
                    error=run.error,
                    tags=run.tags or [],
                    input_preview=input_preview,
                    output_preview=output_preview,
                    model_name=model_name,
                    first_token_s=_compute_ttft(run),
                    first_tool_call_s=first_tool_call_s,
                )
            )
        _list_runs_cache_set(cache_key, summaries)
        return summaries

    async def _build_model_map_via_window(
        self, project_name: str, root_runs: list[Any]
    ) -> dict[str, str]:
        """Single time-window query: get LLM runs in the root-runs' time span,
        match by trace_id in memory. Avoids N trace_id OR filters which hit no
        efficient index on LangSmith (~seconds per query even for N=1).

        Caches per-root results in memory so repeated list_runs pulls
        (pagination, refresh, load-more) don't re-hit LangSmith for roots
        already resolved.
        """
        if not root_runs:
            return {}

        # Short-circuit via cache before any network calls.
        model_map: dict[str, str] = {}
        uncached_roots: list[Any] = []
        for r in root_runs:
            key = f"{project_name}:{r.id}"
            cached = _model_name_cache_get(key)
            if cached is not None:
                if cached:  # non-empty marker means "resolved to this name"
                    model_map[str(r.id)] = cached
            else:
                uncached_roots.append(r)

        if not uncached_roots:
            return model_map

        root_id_set = {str(r.id) for r in uncached_roots}

        # Derive a tight time window around the uncached root runs only.
        starts = [r.start_time for r in uncached_roots if getattr(r, "start_time", None)]
        ends = [r.end_time for r in uncached_roots if getattr(r, "end_time", None)]
        if not starts:
            return model_map
        window_start = min(starts)
        window_end = max(ends) if ends else max(starts)

        model_map: dict[str, str] = {}
        # Single window pull: 100 LLM children usually cover the most recent
        # ~20 roots, which is enough for the "recent page" use case. Paging
        # further is too expensive against LangSmith's trace_id index.
        MAX_ROUNDS = 1
        cursor_end: datetime | None = window_end
        seen_trace_ids: set[str] = set()

        for round_idx in range(MAX_ROUNDS):
            kwargs: dict[str, Any] = {
                "project_name": project_name,
                "run_type": "llm",
                "start_time": window_start,
                "limit": 100,
            }
            if cursor_end is not None:
                kwargs["end_time"] = cursor_end
            try:
                llm_runs = list(await to_thread(self.client.list_runs, **kwargs))
            except Exception as e:
                logger.warning(
                    "LLM window query failed (project=%s, round=%d): %s",
                    project_name, round_idx, e,
                )
                break

            if not llm_runs:
                break

            oldest_start = None
            for llm_run in llm_runs:
                st = getattr(llm_run, "start_time", None)
                if st and (oldest_start is None or st < oldest_start):
                    oldest_start = st
                trace_id = str(llm_run.trace_id) if getattr(llm_run, "trace_id", None) else None
                if not trace_id or trace_id not in root_id_set:
                    continue
                seen_trace_ids.add(trace_id)
                if trace_id in model_map:
                    continue
                extra = llm_run.extra or {}
                metadata = extra.get("metadata", {}) if isinstance(extra, dict) else {}
                name = None
                if isinstance(metadata, dict):
                    name = (
                        metadata.get("ls_model_name")
                        or metadata.get("model_name")
                        or metadata.get("model")
                    )
                if not name and isinstance(extra, dict):
                    inv = extra.get("invocation_params") or {}
                    if isinstance(inv, dict):
                        name = inv.get("model") or inv.get("model_name")
                if name:
                    model_map[trace_id] = str(name)

            missing = root_id_set - seen_trace_ids
            if not missing:
                break
            if oldest_start is None or oldest_start <= window_start:
                break
            cursor_end = oldest_start

        # Write cache entries. Resolved roots get their model name; unresolved
        # roots are negative-cached so subsequent queries skip LangSmith for
        # them entirely (new roots appearing later will miss the cache and
        # trigger a fresh window pull).
        for rid in uncached_roots:
            sid = str(rid.id)
            key = f"{project_name}:{sid}"
            if sid in model_map:
                _model_name_cache_set(key, model_map[sid])
            else:
                _model_name_cache_set(key, "")  # negative cache

        return model_map

    async def _build_model_map(self, project_name: str, root_ids: list) -> dict[str, str]:
        """Query LLM child runs filtered by trace_id and map trace_id -> model_name.

        LangSmith caps `limit` at 100 and child runs for old roots can fall outside
        any recent window, so an unfiltered fetch is both fragile and frequently
        returns an empty map. We batch trace_id filters instead, dispatched in parallel.
        """
        if not root_ids:
            return {}

        str_ids = [str(rid) for rid in root_ids]
        BATCH = 20  # keeps the OR-filter string comfortably small

        async def _fetch_chunk(chunk: list[str]) -> list[Any]:
            flt = (
                f'eq(trace_id, "{chunk[0]}")'
                if len(chunk) == 1
                else "or(" + ",".join([f'eq(trace_id, "{rid}")' for rid in chunk]) + ")"
            )
            try:
                runs = await to_thread(
                    self.client.list_runs,
                    project_name=project_name,
                    run_type="llm",
                    filter=flt,
                    limit=100,
                )
                return list(runs)
            except Exception as e:
                logger.warning(
                    "Failed to fetch LLM child runs for model_map (project=%s, chunk=%d): %s",
                    project_name, len(chunk), e,
                )
                return []

        chunks = [str_ids[i : i + BATCH] for i in range(0, len(str_ids), BATCH)]
        results = await asyncio.gather(*[_fetch_chunk(c) for c in chunks])

        model_map: dict[str, str] = {}
        for chunk_runs in results:
            for llm_run in chunk_runs:
                trace_id = str(llm_run.trace_id) if getattr(llm_run, "trace_id", None) else None
                if not trace_id or trace_id in model_map:
                    continue
                extra = llm_run.extra or {}
                metadata = extra.get("metadata", {}) if isinstance(extra, dict) else {}
                name = None
                if isinstance(metadata, dict):
                    name = (
                        metadata.get("ls_model_name")
                        or metadata.get("model_name")
                        or metadata.get("model")
                    )
                if not name and isinstance(extra, dict):
                    inv = extra.get("invocation_params") or {}
                    if isinstance(inv, dict):
                        name = inv.get("model") or inv.get("model_name")
                if name:
                    model_map[trace_id] = str(name)

        return model_map

    async def fill_enrichments(
        self,
        project_name: str,
        runs: list[dict[str, Any]],
    ) -> tuple[dict[str, str], dict[str, float], list[str]]:
        """Thorough enrichment (model_name + first_tool_call_s) for a set of roots.

        `runs` is a list of `{id, start_time}` dicts — the caller already has
        start_time from its previous list_runs page, so we skip a round-trip
        to LangSmith to re-read roots.

        Phases run in parallel (asyncio.gather): same-project queries on
        LangSmith may be lightly contended, but wall-clock wins from
        overlapping the two big phases comfortably outweigh any contention.

        Both phases use:
          - `select=...` to strip large unused fields (extra/inputs/outputs)
            so each LangSmith page is small;
          - tight time-windows seeded from the caller-provided start_times,
            so rounds walk a small slice of history rather than scanning
            from "now" backwards;
          - dynamic per-round `limit` (capped at 100) sized to remaining
            pending count, since later rounds usually need only a handful
            more matches.

        Negative-cached as -1.0 for tool when no tool child found in the
        scanned window (1h TTL).

        Returns:
            (models, first_tool_calls, missing)
        """
        if not runs:
            return {}, {}, []

        # Parse {id: start_time_dt} once.
        id_to_start: dict[str, datetime | None] = {}
        for r in runs:
            rid = r.get("id")
            if not rid:
                continue
            st = r.get("start_time")
            parsed: datetime | None = None
            if isinstance(st, str):
                try:
                    parsed = datetime.fromisoformat(st.replace("Z", "+00:00"))
                except ValueError:
                    pass
            elif isinstance(st, datetime):
                parsed = st
            id_to_start[str(rid)] = parsed

        # Run the two phases concurrently. They hit different run_type slices
        # on LangSmith so contention is minimal. On a 50-root cold path this
        # roughly halves wall-clock vs. the previous serial approach.
        resolved_models, resolved_tool = await asyncio.gather(
            self._fill_models(project_name, runs, id_to_start),
            self._fill_first_tool(project_name, runs, id_to_start),
        )

        missing_models = [rid for rid in id_to_start if rid not in resolved_models]
        return resolved_models, resolved_tool, missing_models

    async def _fill_models(
        self,
        project_name: str,
        runs: list[dict[str, Any]],
        id_to_start: dict[str, datetime | None],
    ) -> dict[str, str]:
        # Only `extra` carries the model_name (in metadata or invocation_params).
        # Skip inputs/outputs entirely — they're huge LLM payloads we never read.
        SELECT = ["id", "trace_id", "extra", "start_time"]

        resolved: dict[str, str] = {}
        pending: list[dict[str, Any]] = []
        for r in runs:
            rid = r.get("id")
            if not rid:
                continue
            cached = _model_name_cache_get(f"{project_name}:{rid}")
            if cached:
                resolved[rid] = cached
            else:
                # None or "" (negative): retry in thorough mode.
                pending.append(r)
        if not pending:
            return resolved

        pending_ids = {str(r["id"]) for r in pending}
        starts = [id_to_start[str(r["id"])] for r in pending if id_to_start.get(str(r["id"]))]
        if starts:
            window_start = min(starts)
            # Bound the upper end too — without this we'd page back from "now"
            # and waste rounds on unrelated newer LLM calls. +1s buffer covers
            # any clock skew between root.start_time and child.start_time.
            window_end = max(starts) + timedelta(seconds=1)
            cursor_end: datetime | None = window_end
            # 3 rounds × 100 = 300 LLM children covers ~50-80 typical roots.
            # OR-fallback handles the long tail.
            MAX_ROUNDS = 3
            for round_idx in range(MAX_ROUNDS):
                still = pending_ids - set(resolved.keys())
                if not still:
                    break
                # Don't ask for more rows than remaining pending × ~3 (each
                # root usually has 2-4 LLM children).
                page_limit = min(100, max(20, len(still) * 4))
                kwargs: dict[str, Any] = {
                    "project_name": project_name,
                    "run_type": "llm",
                    "start_time": window_start,
                    "limit": page_limit,
                    "select": SELECT,
                }
                if cursor_end is not None:
                    kwargs["end_time"] = cursor_end
                try:
                    llm_runs = list(await to_thread(self.client.list_runs, **kwargs))
                except Exception as e:
                    logger.warning("fill_models: window round %d failed: %s", round_idx, e)
                    break
                if not llm_runs:
                    break
                oldest = None
                for l in llm_runs:
                    st = getattr(l, "start_time", None)
                    if st and (oldest is None or st < oldest):
                        oldest = st
                    tid = str(l.trace_id) if getattr(l, "trace_id", None) else None
                    if not tid or tid not in pending_ids or tid in resolved:
                        continue
                    name = self._model_name_from_run(l)
                    if name:
                        resolved[tid] = name
                if oldest is None or oldest <= window_start:
                    break
                cursor_end = oldest

        # OR-fallback for roots still missing.
        still_missing = pending_ids - set(resolved.keys())
        if still_missing:
            miss_list = list(still_missing)
            miss_chunks = [miss_list[i : i + 5] for i in range(0, len(miss_list), 5)]

            async def _or_chunk(chunk: list[str]) -> list[Any]:
                flt = (
                    f'eq(trace_id, "{chunk[0]}")'
                    if len(chunk) == 1
                    else "or(" + ",".join([f'eq(trace_id, "{i}")' for i in chunk]) + ")"
                )
                try:
                    return list(await to_thread(
                        self.client.list_runs,
                        project_name=project_name,
                        run_type="llm",
                        filter=flt,
                        limit=20,
                        select=SELECT,
                    ))
                except Exception as e:
                    logger.warning("fill_models: OR chunk failed: %s", e)
                    return []

            chunk_results = await asyncio.gather(*[_or_chunk(c) for c in miss_chunks])
            for chunk_runs in chunk_results:
                for l in chunk_runs:
                    tid = str(l.trace_id) if getattr(l, "trace_id", None) else None
                    if not tid or tid in resolved:
                        continue
                    name = self._model_name_from_run(l)
                    if name:
                        resolved[tid] = name

        # Write cache (negative-cache unresolved).
        for r in pending:
            rid = str(r["id"])
            _model_name_cache_set(
                f"{project_name}:{rid}", resolved.get(rid, ""),
            )
        return resolved

    async def _fill_first_tool(
        self,
        project_name: str,
        runs: list[dict[str, Any]],
        id_to_start: dict[str, datetime | None],
    ) -> dict[str, float]:
        # Only need start_time + trace_id to identify earliest tool per trace.
        SELECT = ["id", "trace_id", "start_time"]

        resolved: dict[str, float] = {}
        pending: list[dict[str, Any]] = []
        for r in runs:
            rid = r.get("id")
            if not rid:
                continue
            cached = _first_tool_cache_get(f"{project_name}:{rid}")
            if cached is None:
                pending.append(r)
            elif cached >= 0:
                resolved[rid] = cached
            # cached < 0 = "confirmed no tool" — don't expose, don't re-query.
        if not pending:
            return resolved

        pending_ids = {str(r["id"]) for r in pending}
        found_earliest: dict[str, datetime] = {}
        starts = [id_to_start[str(r["id"])] for r in pending if id_to_start.get(str(r["id"]))]
        if starts:
            window_start = min(starts)
            # +1ms past the newest root start so end_time (exclusive) includes
            # tools that started at exactly that boundary.
            cursor_end = max(starts) + timedelta(microseconds=1)
            # 4 rounds × dynamic limit (≤100) covers ~50-100 roots when
            # tool density is 3-6 calls per root. Early-exit kicks in once
            # every pending root has at least one earliest entry.
            MAX_ROUNDS = 4
            for round_idx in range(MAX_ROUNDS):
                missing = pending_ids - set(found_earliest.keys())
                if not missing:
                    break
                # Tools are usually 3-6 per root; size each page accordingly.
                page_limit = min(100, max(20, len(missing) * 6))
                kwargs = {
                    "project_name": project_name,
                    "run_type": "tool",
                    "start_time": window_start,
                    "limit": page_limit,
                    "select": SELECT,
                }
                if cursor_end is not None:
                    kwargs["end_time"] = cursor_end
                try:
                    tool_runs = list(await to_thread(self.client.list_runs, **kwargs))
                except Exception as e:
                    logger.warning("fill_first_tool: window round %d failed: %s", round_idx, e)
                    break
                if not tool_runs:
                    break
                oldest = None
                for t in tool_runs:
                    st = getattr(t, "start_time", None)
                    if st and (oldest is None or st < oldest):
                        oldest = st
                    tid = str(t.trace_id) if getattr(t, "trace_id", None) else None
                    if not tid or tid not in pending_ids or st is None:
                        continue
                    prev = found_earliest.get(tid)
                    if prev is None or st < prev:
                        found_earliest[tid] = st
                if oldest is None or oldest <= window_start:
                    break
                cursor_end = oldest

        # OR-fallback for roots still missing.
        still_missing = pending_ids - set(found_earliest.keys())
        if still_missing:
            miss_list = list(still_missing)
            miss_chunks = [miss_list[i : i + 5] for i in range(0, len(miss_list), 5)]

            async def _or_chunk(chunk: list[str]) -> list[Any]:
                flt = (
                    f'eq(trace_id, "{chunk[0]}")'
                    if len(chunk) == 1
                    else "or(" + ",".join([f'eq(trace_id, "{i}")' for i in chunk]) + ")"
                )
                try:
                    return list(await to_thread(
                        self.client.list_runs,
                        project_name=project_name,
                        run_type="tool",
                        filter=flt,
                        limit=30,
                        select=SELECT,
                    ))
                except Exception as e:
                    logger.warning("fill_first_tool: OR chunk failed: %s", e)
                    return []

            chunk_results = await asyncio.gather(*[_or_chunk(c) for c in miss_chunks])
            for chunk_runs in chunk_results:
                for t in chunk_runs:
                    st = getattr(t, "start_time", None)
                    tid = str(t.trace_id) if getattr(t, "trace_id", None) else None
                    if not tid or tid not in pending_ids or st is None:
                        continue
                    prev = found_earliest.get(tid)
                    if prev is None or st < prev:
                        found_earliest[tid] = st

        # Compute seconds-from-root-start; tz-naive arithmetic for safety.
        for tid, tool_start in found_earliest.items():
            root_start = id_to_start.get(tid)
            if root_start is None:
                continue
            try:
                a = tool_start.replace(tzinfo=None) if tool_start.tzinfo else tool_start
                b = root_start.replace(tzinfo=None) if root_start.tzinfo else root_start
                delta = (a - b).total_seconds()
                if delta >= 0:
                    resolved[tid] = delta
            except Exception:
                pass

        # Cache writes: resolved → positive; unresolved → -1.0 (1h negative cache).
        for r in pending:
            rid = str(r["id"])
            if rid in resolved:
                _first_tool_cache_set(f"{project_name}:{rid}", resolved[rid])
            else:
                _first_tool_cache_set(f"{project_name}:{rid}", -1.0)
        return resolved

    @staticmethod
    def _model_name_from_run(llm_run: Any) -> str:
        extra = llm_run.extra or {}
        metadata = extra.get("metadata", {}) if isinstance(extra, dict) else {}
        name = None
        if isinstance(metadata, dict):
            name = (
                metadata.get("ls_model_name")
                or metadata.get("model_name")
                or metadata.get("model")
            )
        if not name and isinstance(extra, dict):
            inv = extra.get("invocation_params") or {}
            if isinstance(inv, dict):
                name = inv.get("model") or inv.get("model_name")
        return str(name) if name else ""

    async def extract_test_cases(
        self,
        run_ids: list[str],
        *,
        source: str = "trace_derived",
        default_tags: list[str] | None = None,
        include_output_as_expected: bool = False,
        concurrency: int = 20,
    ) -> list[TestCase]:
        sem = asyncio.Semaphore(concurrency)

        async def _extract_one(run_id: str) -> TestCase:
            async with sem:
                run = await to_thread(self.client.read_run, run_id=run_id)
                return await self._run_to_test_case(
                    run,
                    source=source,
                    default_tags=default_tags or [],
                    include_output_as_expected=include_output_as_expected,
                )

        cases = await asyncio.gather(*[_extract_one(rid) for rid in run_ids])
        return list(cases)

    async def extract_test_cases_fast(
        self,
        project_name: str,
        run_ids: list[str],
        *,
        source: str = "trace_derived",
        default_tags: list[str] | None = None,
        include_output_as_expected: bool = False,
    ) -> list[TestCase]:
        """Fast batch extraction: fetches recent runs from the project and
        matches by ID in memory. Skips child tool-call extraction for speed."""
        id_set = set(run_ids)

        runs = await to_thread(
            self.client.list_runs,
            project_name=project_name,
            is_root=True,
            limit=100,
        )

        cases: list[TestCase] = []
        for run in runs:
            if str(run.id) in id_set:
                case = self._run_to_test_case_sync(
                    run,
                    source=source,
                    default_tags=default_tags or [],
                    include_output_as_expected=include_output_as_expected,
                )
                cases.append(case)
                if len(cases) == len(run_ids):
                    break
        return cases

    def _run_to_test_case_sync(
        self, run: Any, *, source: str, default_tags: list[str], include_output_as_expected: bool
    ) -> TestCase:
        """Synchronous version that skips tool-call extraction for fast import."""
        messages = (run.inputs or {}).get("messages", [])
        if not messages:
            input_val = run.inputs or {}
            if "input" in input_val:
                messages = [{"role": "user", "content": str(input_val["input"])}]
            elif "question" in input_val:
                messages = [{"role": "user", "content": str(input_val["question"])}]
            else:
                messages = [{"role": "user", "content": str(input_val)}]

        input_messages = normalize_messages(messages)
        # For fast import, keep only the last user message to avoid huge payloads
        user_messages = [m for m in input_messages if m.get("role") == "user"]
        if user_messages:
            input_messages = [user_messages[-1]]

        max_latency_ms = int(run.latency * 1000 * 1.5) if run.latency else None
        max_tokens = int(run.total_tokens * 1.2) if run.total_tokens else None

        case = TestCase(
            dataset_version="",
            name=f"trace-{run.name or 'run'}-{str(run.id)[:8]}",
            description=f"Extracted from run {run.id}",
            source=source,
            tags=default_tags,
            input_messages=input_messages,
            max_latency_ms=max_latency_ms,
            max_tokens=max_tokens,
        )

        if include_output_as_expected and run.outputs:
            output_text = run.outputs.get("output", run.outputs.get("text", ""))
            if isinstance(output_text, str) and output_text:
                case.expected_output = output_text

        return case

    async def _run_to_test_case(
        self, run: Any, *, source: str, default_tags: list[str], include_output_as_expected: bool
    ) -> TestCase:
        messages = (run.inputs or {}).get("messages", [])
        if not messages:
            input_val = run.inputs or {}
            if "input" in input_val:
                messages = [{"role": "user", "content": str(input_val["input"])}]
            elif "question" in input_val:
                messages = [{"role": "user", "content": str(input_val["question"])}]
            else:
                messages = [{"role": "user", "content": str(input_val)}]

        input_messages = normalize_messages(messages)

        tool_calls = await self._extract_tool_calls(run)

        max_latency_ms = int(run.latency * 1000 * 1.5) if run.latency else None
        max_tokens = int(run.total_tokens * 1.2) if run.total_tokens else None

        case = TestCase(
            dataset_version="",
            name=f"trace-{run.name or 'run'}-{str(run.id)[:8]}",
            description=f"Extracted from run {run.id}",
            source=source,
            tags=default_tags,
            input_messages=input_messages,
            expected_tool_calls=tool_calls,
            max_latency_ms=max_latency_ms,
            max_tokens=max_tokens,
        )

        if include_output_as_expected and run.outputs:
            output_text = run.outputs.get("output", run.outputs.get("text", ""))
            if isinstance(output_text, str) and output_text:
                case.expected_output = output_text

        return case

    async def _extract_tool_calls(self, run: Any) -> list[ToolCallExpectation]:
        child_ids = getattr(run, "child_run_ids", None)
        if not child_ids:
            return []

        child_runs = await to_thread(
            self.client.list_runs,
            run_ids=child_ids,
            run_type="tool",
        )

        tool_calls = []
        for i, child in enumerate(child_runs):
            tool_calls.append(
                ToolCallExpectation(
                    tool_name=child.name or "",
                    args_matcher=child.inputs if isinstance(child.inputs, dict) else None,
                    order=i,
                    required=True,
                )
            )
        return tool_calls

    async def get_run_detail(
        self, run_id: str, project_name: str | None = None
    ) -> dict[str, Any]:
        """Fetch full content of a single run + direct-child metadata.

        Used by the Traces detail modal for lazy tree expansion.
        `project_name` is optional — when provided, child listing uses a
        `parent_run_id` server-side filter which is cheaper than an id-set query.

        Results are cached in-memory with a 5-minute TTL because LangSmith runs
        are immutable history; repeated clicks on the same node / re-opens of
        the same modal hit the cache and skip the network round-trips entirely.
        """
        cache_key = f"{run_id}:{project_name or ''}"
        async with _detail_cache_lock:
            cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        run = await to_thread(self.client.read_run, run_id=run_id)

        child_ids = list(getattr(run, "child_run_ids", None) or [])
        children_truncated = len(child_ids) > 100

        child_runs: list[Any] = []
        if child_ids:
            try:
                if project_name:
                    flt = f'eq(parent_run_id, "{run_id}")'
                    child_runs = list(await to_thread(
                        self.client.list_runs,
                        project_name=project_name,
                        filter=flt,
                        limit=100,
                    ))
                else:
                    child_runs = list(await to_thread(
                        self.client.list_runs,
                        run_ids=child_ids[:100],
                    ))
            except Exception as e:
                logger.warning(
                    "Failed to fetch child runs for %s (project=%s): %s",
                    run_id, project_name, e,
                )
                child_runs = []

        child_runs.sort(key=lambda r: getattr(r, "start_time", None) or datetime.min)

        children_meta: list[dict[str, Any]] = []
        for c in child_runs:
            c_child_ids = getattr(c, "child_run_ids", None)
            # list_runs often returns child_run_ids=None even when the child has
            # its own descendants (the field is only populated by read_run).
            # Default to True so the UI always offers an expand affordance —
            # the subsequent detail fetch will reveal the real structure.
            has_children = True if c_child_ids is None else bool(c_child_ids)
            children_meta.append({
                "id": str(c.id),
                "name": c.name or "",
                "run_type": getattr(c, "run_type", "") or "",
                "status": c.status or "unknown",
                "start_time": getattr(c, "start_time", None),
                "latency_s": getattr(c, "latency", None),
                "total_tokens": getattr(c, "total_tokens", None),
                "error": getattr(c, "error", None),
                "has_children": has_children,
            })

        extra = run.extra or {}
        if not isinstance(extra, dict):
            extra = {}
        metadata = extra.get("metadata") if isinstance(extra.get("metadata"), dict) else None
        extra_rest = {k: v for k, v in extra.items() if k != "metadata"} or None

        inputs = run.inputs if isinstance(run.inputs, dict) else ({"_raw": run.inputs} if run.inputs else None)
        outputs = run.outputs if isinstance(run.outputs, dict) else ({"_raw": run.outputs} if run.outputs else None)

        result = {
            "id": str(run.id),
            "name": run.name or "",
            "run_type": getattr(run, "run_type", "") or "",
            "status": run.status or "unknown",
            "start_time": getattr(run, "start_time", None),
            "end_time": getattr(run, "end_time", None),
            "latency_s": getattr(run, "latency", None),
            "prompt_tokens": getattr(run, "prompt_tokens", None),
            "completion_tokens": getattr(run, "completion_tokens", None),
            "total_tokens": getattr(run, "total_tokens", None),
            "error": getattr(run, "error", None),
            "inputs": inputs,
            "outputs": outputs,
            "extra": extra_rest,
            "metadata": metadata,
            "tags": list(getattr(run, "tags", None) or []),
            "parent_run_id": str(run.parent_run_id) if getattr(run, "parent_run_id", None) else None,
            "trace_id": str(run.trace_id) if getattr(run, "trace_id", None) else None,
            "children": children_meta,
            "children_truncated": children_truncated,
        }
        async with _detail_cache_lock:
            _cache_set(cache_key, result)
        return result
