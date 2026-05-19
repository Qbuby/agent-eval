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

from agent_eval.config import settings

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

    Each case becomes a fresh Langfuse trace with:
        - input  = {"question": ...}
        - output = actual_output text
        - metadata = {run_id, case_id, status, latency_ms, langsmith_run_id, ...}
    Each evaluator dimension becomes one ``score_trace`` call attached to
    that trace.

    Side effects:
        - Persists the new Langfuse trace_id back to ``test_results.langfuse_trace_id``
          (matched by thread_id) so the detail page and the post-run pull-back
          can find it without a second API roundtrip.

    Skips silently when LANGFUSE_REMOTE_WRITE is off or Langfuse isn't
    configured. Returns ``{traces: int, scores: int, errors: int}``.
    """
    stats = {"traces": 0, "scores": 0, "errors": 0}

    if not settings.langfuse.remote_write or not settings.langfuse.configured:
        logger.info("langfuse-sync: remote_write off or not configured, skipping")
        return stats

    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning("langfuse-sync: SDK not installed")
        return stats

    client = Langfuse(
        public_key=settings.langfuse.public_key,
        secret_key=settings.langfuse.secret_key,
        host=settings.langfuse.host,
    )

    loop = asyncio.get_event_loop()

    def _push_one(res: dict[str, Any]) -> tuple[int, int, int, str | None]:
        """Run in a thread — the SDK is blocking.
        Returns (traces, scores, errors, trace_id_or_none).
        """
        traces = scores = errors = 0
        scores_dict: dict[str, float] = res.get("scores") or {}
        if not scores_dict:
            return (0, 0, 0, None)

        # Make sure score-configs exist before writing scores against them.
        for name in scores_dict.keys():
            _ensure_score_config_sync(client, name)

        # New trace per case. Trace id is opaque to Langfuse — we generate
        # one per case and stash it back in the DB so we can later pull
        # evaluator scores by trace_id.
        trace_id = uuid.uuid4().hex[:32]
        case_name = res.get("case_name") or res.get("case_id") or "case"
        try:
            with client.start_as_current_span(
                name=f"eval/{case_name}",
                trace_context={"trace_id": trace_id},
                input={"question": res.get("question", "")},
            ) as span:
                span.update_trace(
                    name=f"eval/{run_name or run_id[:8]}/{case_name}",
                    output=res.get("actual_output") or "",
                    metadata={
                        "agent_eval.run_id": run_id,
                        "agent_eval.run_name": run_name,
                        "agent_eval.case_id": res.get("case_id"),
                        "agent_eval.case_name": case_name,
                        "agent_eval.status": res.get("status"),
                        "agent_eval.latency_ms": res.get("latency_ms"),
                        "agent_eval.thread_id": res.get("thread_id"),
                        "agent_eval.langsmith_run_id": res.get("langsmith_run_id"),
                    },
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
    if not settings.langfuse.configured:
        return out

    import httpx
    import base64
    from sqlalchemy import select
    from agent_eval.db import async_session_factory
    from agent_eval.db_models.tables import (
        TestResultRow, EvaluationScoreRow,
    )

    auth = base64.b64encode(
        f"{settings.langfuse.public_key}:{settings.langfuse.secret_key}".encode()
    ).decode()
    headers = {"Authorization": f"Basic {auth}"}
    base = settings.langfuse.host.rstrip("/")

    seen_keys: set[tuple[uuid.UUID, str]] = set()
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

        # 2. For each trace_id, query Langfuse scores
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as http:
            for trace_id, result_id in trace_to_result.items():
                try:
                    r = await http.get(
                        f"{base}/api/public/scores",
                        params={"traceId": trace_id, "limit": 50},
                    )
                    r.raise_for_status()
                    items = r.json().get("data") or []
                except Exception as e:  # noqa: BLE001
                    logger.warning("langfuse-pull score query for %s: %s",
                                   trace_id, str(e)[:200])
                    continue

                for s in items:
                    if s.get("source") != "EVAL":
                        continue  # skip our own API-source pushes
                    name = s.get("name") or ""
                    value = s.get("value")
                    if value is None:
                        continue
                    key = (result_id, f"langfuse:{name}")
                    if key in seen_keys:
                        continue

                    # 3. Upsert into evaluation_scores
                    async with async_session_factory() as session2:
                        existing = (await session2.execute(
                            select(EvaluationScoreRow)
                            .where(EvaluationScoreRow.result_id == result_id)
                            .where(EvaluationScoreRow.dimension == f"langfuse:{name}")
                        )).scalar_one_or_none()
                        if existing is None:
                            session2.add(EvaluationScoreRow(
                                result_id=result_id,
                                dimension=f"langfuse:{name}",
                                score=float(value),
                                weight=1.0,
                                weighted_score=float(value),
                                scoring_method="langfuse-eval",
                                details={"comment": s.get("comment") or "",
                                         "source": "EVAL",
                                         "trace_id": trace_id},
                            ))
                        else:
                            existing.score = float(value)
                            existing.weighted_score = float(value)
                        await session2.commit()
                    seen_keys.add(key)
                    out["pulled"] += 1

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
