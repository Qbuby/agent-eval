"""Push eval scores to Langfuse so they show up alongside traces in its UI.

Langfuse's public API does NOT expose evaluator template / LLM-judge config
endpoints — those live in the commercial control plane. What it DOES expose
that we can use:

- ``POST /api/public/score-configs`` — register the *shape* of a score
  (NUMERIC 0-1 in our case)
- ``POST`` (via SDK ``score_trace``) — write a score onto an existing trace
- Trace creation via SDK ``start_as_current_span`` + ``update_trace``

The agent's real LangChain trace already lives in LangSmith; we don't try to
mirror it. Instead, on each completed eval run we post a *synthetic* trace to
Langfuse with the question / output / metadata, and attach every evaluator
dimension as a score. That gives Langfuse users a per-case score view
without any agent-side code change.

Fire-and-forget from the runner: failures only log, never block the run.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from agent_eval.config_service import config_service

logger = logging.getLogger(__name__)

# Cache score-config names we've already created this process — first-write
# wins; same name reused across runs hits the cache and skips the round-trip.
_SCORE_CONFIG_CACHE: set[str] = set()


def _ensure_score_config_sync(client: Any, name: str) -> None:
    """Create a NUMERIC 0-1 score-config if it doesn't already exist.

    Runs synchronously in a thread because the Langfuse SDK exposes the
    score-config endpoints via its low-level ``api`` namespace which is
    blocking. Idempotent on the server (it 200s on duplicate name + same
    shape; if a different shape already exists we just log and skip).
    """
    if name in _SCORE_CONFIG_CACHE:
        return
    try:
        # Langfuse SDK 3.x exposes the public REST API under client.api
        client.api.score_configs.create(
            name=name,
            data_type="NUMERIC",
            min_value=0.0,
            max_value=1.0,
            description=f"agent-eval evaluator dimension: {name}",
        )
        _SCORE_CONFIG_CACHE.add(name)
        logger.info("langfuse score-config created: %s", name)
    except Exception as e:  # noqa: BLE001
        # Most common: 409 / 400 because the name already exists with a
        # different dataType (e.g. someone hand-created it as CATEGORICAL).
        # Don't fail the whole sync — just stop trying for this name.
        logger.warning("langfuse score-config create skipped for %s: %s", name, str(e)[:200])
        _SCORE_CONFIG_CACHE.add(name)


async def sync_run_scores_to_langfuse(
    *,
    run_id: str,
    run_name: str | None,
    per_case_results: list[dict[str, Any]],
    extra_tags: list[str] | None = None,
) -> dict[str, int]:
    """Push every (case → score) pair from a finished run to Langfuse.

    ``extra_tags`` are stamped onto every trace alongside the standard
    ['agent-eval', 'run:<id>'] tags. The runner uses this to forward each
    selected evaluator's tag (e.g. 'agent-eval-correctness') so that
    Langfuse-side evaluators bound to those tags pick the trace up.

    Each case becomes a fresh Langfuse trace whose only payload-bearing
    child is a single SPAN. All three evaluator variables are read off
    that span (Run on = Observations, target type = SPAN):

        - span.input    = {"question": ...}             → query        = input + "question"
        - span.output   = {"answer": ...}               → generation   = output + "answer"
        - span.metadata = {"expected_output": ...,
                           "expected_tool_calls": [...],
                           ... bookkeeping ...}         → ground_truth = metadata + "expected_output"

    The trace itself only carries name + tags so list views and filters
    keep working; we no longer mirror output/metadata onto the trace.
    Each locally computed evaluator dimension becomes one ``score_trace``
    call attached to that trace.

    Side effects:
        - Persists the new Langfuse trace_id back to ``test_results.langfuse_trace_id``
          (matched by thread_id) so the detail page and the post-run pull-back
          can find it without a second API roundtrip.

    Skips silently when LANGFUSE_REMOTE_WRITE is off or Langfuse isn't
    configured. Returns ``{traces: int, scores: int, errors: int}``.
    """
    stats = {"traces": 0, "scores": 0, "errors": 0}

    # Resolve the active Langfuse connection preset (langfuse.connection),
    # falling back to env settings. remote_write also comes from the preset.
    conn = await config_service.get_langfuse_connection()
    if not conn["remote_write"] or not conn["configured"]:
        logger.info("langfuse-sync: remote_write off or not configured, skipping")
        return stats

    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning("langfuse-sync: SDK not installed")
        return stats

    client = Langfuse(
        public_key=conn["public_key"],
        secret_key=conn["secret_key"],
        host=conn["host"],
    )

    loop = asyncio.get_event_loop()

    def _push_one(res: dict[str, Any]) -> tuple[int, int, int, str | None]:
        """Run in a thread — the SDK is blocking.
        Returns (traces, scores, errors, trace_id_or_none).

        We always push a trace when at least one of:
          - the case carries local scores (legacy mode), OR
          - the runner forwarded any extra_tags (tag-only mode — the
            whole point IS to stamp the tag onto the trace).
        Otherwise we'd have nothing for Langfuse-side evaluators to
        latch onto, and ``traces=0 scores=0`` would silently mask the
        skipped sync.
        """
        traces = scores = errors = 0
        scores_dict: dict[str, float] = res.get("scores") or {}
        if not scores_dict and not extra_tags:
            return (0, 0, 0, None)

        # Make sure score-configs exist before writing scores against them.
        for name in scores_dict.keys():
            _ensure_score_config_sync(client, name)

        # New trace per case. Trace id is opaque to Langfuse — we generate
        # one per case and stash it back in the DB so we can later pull
        # evaluator scores by trace_id.
        trace_id = uuid.uuid4().hex[:32]
        case_name = res.get("case_name") or res.get("case_id") or "case"
        # We only run Observation-level evaluators now, so all three variables
        # the evaluator reads (query / generation / ground_truth) come from
        # the span itself. The trace just carries the name + tags so filters
        # and UI deep-links still work.
        #
        # Variable mapping in the Langfuse evaluator config:
        #   query        → Input    + field "question"
        #   generation   → Output   + field "answer"
        #   ground_truth → Metadata + field "expected_output"
        expected_output = res.get("expected_output") or ""
        expected_tool_calls = res.get("expected_tool_calls") or []
        actual_output = res.get("actual_output") or ""
        try:
            with client.start_as_current_span(
                name=f"eval/{case_name}",
                trace_context={"trace_id": trace_id},
                input={"question": res.get("question", "")},
            ) as span:
                span.update(
                    output={"answer": actual_output},
                    metadata={
                        "expected_output": expected_output,
                        "expected_tool_calls": expected_tool_calls,
                        "run_id": run_id,
                        "run_name": run_name,
                        "case_id": res.get("case_id"),
                        "case_name": case_name,
                        "status": res.get("status"),
                        "latency_ms": res.get("latency_ms"),
                        "thread_id": res.get("thread_id"),
                        "langsmith_run_id": res.get("langsmith_run_id"),
                    },
                )
                # Trace gets only what's needed for filtering and listing.
                span.update_trace(
                    name=f"eval/{run_name or run_id[:8]}/{case_name}",
                    tags=["agent-eval", f"run:{run_id[:8]}", *(extra_tags or [])],
                )
                traces += 1
                for dim_name, value in scores_dict.items():
                    try:
                        span.score_trace(
                            name=dim_name,
                            value=float(value),
                            comment=f"agent-eval run {run_id[:8]} case {case_name}",
                        )
                        scores += 1
                    except Exception as e:  # noqa: BLE001
                        logger.warning("langfuse-sync score push %s/%s: %s",
                                       case_name, dim_name, str(e)[:200])
                        errors += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("langfuse-sync trace push %s: %s", case_name, str(e)[:200])
            errors += 1
            return (traces, scores, errors, None)
        return (traces, scores, errors, trace_id)

    # Run pushes in parallel via the executor — Langfuse SDK is blocking, but
    # it batches under the hood and the network legs can overlap.
    results = await asyncio.gather(*[
        loop.run_in_executor(None, _push_one, r) for r in per_case_results
    ])

    # Persist trace_ids back so /api/eval/results/{id} reads them.
    from sqlalchemy import select, update
    from agent_eval.db import async_session_factory
    from agent_eval.db_models.tables import TestResultRow
    async with async_session_factory() as session:
        for res, (t, s, e, trace_id) in zip(per_case_results, results):
            stats["traces"] += t
            stats["scores"] += s
            stats["errors"] += e
            if not trace_id:
                continue
            thread_id = res.get("thread_id")
            if not thread_id:
                continue
            await session.execute(
                update(TestResultRow)
                .where(TestResultRow.run_id == uuid.UUID(run_id))
                .where(TestResultRow.thread_id == thread_id)
                .values(langfuse_trace_id=trace_id)
            )
        await session.commit()

    # Flush the SDK's internal queue so traces / scores actually leave the
    # process before this function returns. Without it, a short-lived task
    # can drop the buffered events.
    try:
        await loop.run_in_executor(None, client.flush)
    except Exception as e:  # noqa: BLE001
        logger.warning("langfuse-sync flush: %s", str(e)[:200])

    logger.info(
        "langfuse-sync: run=%s traces=%d scores=%d errors=%d",
        run_id, stats["traces"], stats["scores"], stats["errors"],
    )
    return stats


# ─── Pull-back: read Langfuse evaluator scores back into our DB ──────────


async def pull_evaluator_scores_for_run(
    *, run_id: str, max_attempts: int = 10, interval_seconds: int = 30,
) -> dict[str, int]:
    """Poll Langfuse for evaluator-produced scores and stamp them onto our
    eval results.

    Langfuse worker latency is typically 1-5 minutes for a freshly synced
    trace. We poll up to ``max_attempts × interval_seconds`` (default
    5 minutes) and exit early once the count of new EVAL-source scores
    stops growing for two consecutive polls.

    Each Langfuse score with ``source == "EVAL"`` (i.e. produced by a
    Langfuse-configured evaluator) is written to ``evaluation_scores``
    with ``dimension = "langfuse:<score_name>"`` so the detail page can
    show them next to our locally computed scores without a name clash.

    Returns ``{polls: int, pulled: int}`` — how many EVAL-source scores
    were imported in total.
    """
    out = {"polls": 0, "pulled": 0}
    conn = await config_service.get_langfuse_connection()
    if not conn["configured"]:
        return out

    import httpx
    import base64
    from collections import defaultdict
    from sqlalchemy import select
    from agent_eval.db import async_session_factory
    from agent_eval.db_models.tables import (
        TestResultRow, EvaluationScoreRow,
    )

    auth = base64.b64encode(
        f"{conn['public_key']}:{conn['secret_key']}".encode()
    ).decode()
    headers = {"Authorization": f"Basic {auth}"}
    base = conn["host"].rstrip("/")

    last_pulled = 0
    stale_polls = 0

    for attempt in range(1, max_attempts + 1):
        out["polls"] = attempt

        # 1. Get all results for this run that have a langfuse_trace_id
        async with async_session_factory() as session:
            results = (await session.execute(
                select(TestResultRow)
                .where(TestResultRow.run_id == uuid.UUID(run_id))
                .where(TestResultRow.langfuse_trace_id.isnot(None))
            )).scalars().all()

            if not results:
                logger.info("langfuse-pull: no traces synced yet for run %s, skipping", run_id)
                return out

            trace_to_result: dict[str, uuid.UUID] = {
                r.langfuse_trace_id: r.id for r in results if r.langfuse_trace_id
            }

        # 2. Pull scores for the whole project, paginated. The Langfuse build
        #    we target IGNORES the server-side `traceId` filter, so we filter
        #    client-side by `s.traceId` against the trace_ids we care about.
        trace_ids = set(trace_to_result.keys())
        # (trace_id, dim) → list of observation-level score values
        buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
        # Most recent comment per bucket (for the details JSON)
        bucket_meta: dict[tuple[str, str], dict[str, Any]] = {}

        async with httpx.AsyncClient(timeout=15.0, headers=headers) as http:
            page = 1
            while True:
                try:
                    r = await http.get(
                        f"{base}/api/public/scores",
                        params={"limit": 100, "page": page},
                    )
                    r.raise_for_status()
                    body = r.json()
                except Exception as e:  # noqa: BLE001
                    logger.warning("langfuse-pull list page=%d: %s", page, str(e)[:200])
                    break
                items = body.get("data") or []
                if not items:
                    break
                for s in items:
                    tid = s.get("traceId")
                    if tid not in trace_ids:
                        continue
                    # Observation-level only — trace-level scores were judged
                    # against an empty trace.output and aren't useful.
                    obs_id = s.get("observationId")
                    if not obs_id:
                        continue
                    src = s.get("source") or ""
                    comment = s.get("comment") or ""
                    # Skip our own outbound score_trace pushes.
                    if src == "API" and comment.startswith("agent-eval run"):
                        continue
                    name = s.get("name") or ""
                    value = s.get("value")
                    if name == "" or value is None:
                        continue
                    key = (tid, name)
                    buckets[key].append(float(value))
                    bucket_meta[key] = {
                        "comment": comment,
                        "source": src,
                        "observation_id": obs_id,
                    }
                meta = body.get("meta") or {}
                total_pages = meta.get("totalPages") or 1
                if page >= total_pages:
                    break
                page += 1

        # 3. Upsert: one row per (case, dimension), value = mean of all
        #    observation-level scores in that bucket.
        if buckets:
            logger.info(
                "langfuse-pull: run=%s buckets=%d sample=%s",
                run_id, len(buckets),
                {f"{k[0][:8]}/{k[1]}": (round(sum(v) / len(v), 3), len(v))
                 for k, v in list(buckets.items())[:6]},
            )
            async with async_session_factory() as session2:
                for (trace_id, name), values in buckets.items():
                    result_id = trace_to_result.get(trace_id)
                    if result_id is None:
                        continue
                    avg = sum(values) / len(values)
                    details = {
                        "mean": avg,
                        "count": len(values),
                        "values": values,
                        "trace_id": trace_id,
                        **bucket_meta.get((trace_id, name), {}),
                    }
                    existing = (await session2.execute(
                        select(EvaluationScoreRow)
                        .where(EvaluationScoreRow.result_id == result_id)
                        .where(EvaluationScoreRow.dimension == f"langfuse:{name}")
                    )).scalar_one_or_none()
                    if existing is None:
                        session2.add(EvaluationScoreRow(
                            result_id=result_id,
                            dimension=f"langfuse:{name}",
                            score=avg,
                            weight=1.0,
                            weighted_score=avg,
                            scoring_method="langfuse-eval",
                            details=details,
                        ))
                        out["pulled"] += 1
                    else:
                        existing.score = avg
                        existing.weighted_score = avg
                        existing.details = details
                await session2.commit()

        # 4. Early exit if no new scores landed in this poll
        if out["pulled"] == last_pulled:
            stale_polls += 1
            if stale_polls >= 2 and out["pulled"] > 0:
                logger.info("langfuse-pull: stable at %d, stopping after poll %d",
                            out["pulled"], attempt)
                break
        else:
            stale_polls = 0
        last_pulled = out["pulled"]

        if attempt < max_attempts:
            await asyncio.sleep(interval_seconds)

    logger.info("langfuse-pull: run=%s polls=%d pulled=%d",
                run_id, out["polls"], out["pulled"])
    return out
