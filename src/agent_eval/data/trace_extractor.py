from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
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
_LIST_RUNS_CACHE_MAX = 64
_LIST_RUNS_CACHE_TTL_S = 30
_list_runs_cache: "OrderedDict[str, tuple[float, list]]" = OrderedDict()

# Per-(project, root_id) model_name cache. Immutable history → long TTL is safe.
_MODEL_NAME_CACHE_MAX = 4096
_MODEL_NAME_CACHE_TTL_S = 3600
_model_name_cache: "OrderedDict[str, tuple[float, str]]" = OrderedDict()


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
    ) -> list[RunSummary]:
        # Cache key captures everything that affects the query result.
        cache_key = (
            f"{project_name}|{start_time.isoformat() if start_time else ''}"
            f"|{end_time.isoformat() if end_time else ''}|{status or ''}"
            f"|{','.join(sorted(tags or []))}|{limit}"
        )
        cached = _list_runs_cache_get(cache_key)
        if cached is not None:
            return cached

        kwargs: dict[str, Any] = {
            "project_name": project_name,
            "is_root": True,
            "limit": limit,
        }
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

        # Fetch LLM child runs to extract model_name for each root run.
        # Single time-window query instead of N trace_id OR filters (10x faster
        # against LangSmith — OR(eq(trace_id, ...)) doesn't hit any efficient
        # index and each clause costs ~seconds even for low-N queries).
        model_map = await self._build_model_map_via_window(project_name, root_runs)

        summaries = []
        for run in root_runs:
            input_preview = truncate(str(run.inputs or {}), 120)
            output_preview = truncate(str(run.outputs or {}), 120)
            model_name = model_map.get(str(run.id), "")
            summaries.append(
                RunSummary(
                    id=str(run.id),
                    name=run.name or "",
                    status=run.status or "unknown",
                    start_time=run.start_time,
                    latency_s=run.latency,
                    total_tokens=run.total_tokens,
                    error=run.error,
                    tags=run.tags or [],
                    input_preview=input_preview,
                    output_preview=output_preview,
                    model_name=model_name,
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

    async def fill_models(
        self,
        project_name: str,
        runs: list[dict[str, Any]],
    ) -> tuple[dict[str, str], list[str]]:
        """Thorough model_name resolution for a known set of root run ids.

        `runs` is a list of `{id, start_time}` dicts — the caller already has
        start_time from its previous list_runs page, so we skip a round-trip
        to LangSmith to re-read roots. (LangSmith's runs/query endpoint also
        rejects or(eq(id, ...)) filters across different "tables", so there
        is no cheap way to bulk-read roots by id.)

        Strategy (cache-aware):
        1. Short-circuit ids already in _model_name_cache
        2. Walk back time-window LLM queries (5 rounds, each limit=100)
        3. For any root still missing, OR-5 trace_id batches as fallback

        Slower than list_runs (~30-75s cold), but covers ~100% when LangSmith
        has the data. Results cache for 1h, so repeat calls and later
        list_runs of the same project return instantly.
        """
        if not runs:
            return {}, []

        resolved: dict[str, str] = {}
        pending: list[dict[str, Any]] = []
        for r in runs:
            rid = r.get("id")
            if not rid:
                continue
            key = f"{project_name}:{rid}"
            cached = _model_name_cache_get(key)
            if cached:
                resolved[rid] = cached
            elif cached is None:
                pending.append(r)
            else:
                # Negative-cached; retry in thorough mode.
                pending.append(r)

        if not pending:
            return resolved, []

        # Bound the time window from supplied start_times.
        starts: list[datetime] = []
        for r in pending:
            st = r.get("start_time")
            if isinstance(st, str):
                try:
                    starts.append(datetime.fromisoformat(st.replace("Z", "+00:00")))
                except ValueError:
                    pass
            elif isinstance(st, datetime):
                starts.append(st)

        pending_ids = {str(r["id"]) for r in pending}

        if starts:
            window_start = min(starts)
            window_end = max(starts)
            cursor_end: datetime | None = window_end
            MAX_ROUNDS = 5

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
                        "fill_models: window round %d failed: %s", round_idx, e,
                    )
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

                still_missing = pending_ids - set(resolved.keys())
                if not still_missing:
                    break
                if oldest is None or oldest <= window_start:
                    break
                cursor_end = oldest

        still_missing = pending_ids - set(resolved.keys())

        # OR-filter fallback for roots not covered by any time window (or when
        # the caller supplied no start_times at all).
        if still_missing:
            BATCH_OR = 5
            miss_list = list(still_missing)
            miss_chunks = [miss_list[i : i + BATCH_OR] for i in range(0, len(miss_list), BATCH_OR)]
            for chunk in miss_chunks:
                flt = (
                    f'eq(trace_id, "{chunk[0]}")'
                    if len(chunk) == 1
                    else "or(" + ",".join([f'eq(trace_id, "{i}")' for i in chunk]) + ")"
                )
                try:
                    llm_runs = list(await to_thread(
                        self.client.list_runs,
                        project_name=project_name,
                        run_type="llm",
                        filter=flt,
                        limit=100,
                    ))
                except Exception as e:
                    logger.warning("fill_models: OR fallback chunk failed: %s", e)
                    continue
                for l in llm_runs:
                    tid = str(l.trace_id) if getattr(l, "trace_id", None) else None
                    if not tid or tid in resolved:
                        continue
                    name = self._model_name_from_run(l)
                    if name:
                        resolved[tid] = name

        # Persist to cache. Resolved → name; still-missing → negative cache so
        # thorough re-runs don't keep paying the cost. Negative entries expire
        # with the 1h TTL, so brand-new LLM data gets picked up eventually.
        for r in pending:
            rid = str(r["id"])
            key = f"{project_name}:{rid}"
            _model_name_cache_set(key, resolved.get(rid, ""))

        missing_out = [str(r["id"]) for r in pending if str(r["id"]) not in resolved]
        return resolved, missing_out

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
