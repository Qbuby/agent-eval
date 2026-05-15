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
) -> dict[str, int]:
    """Push every (case → score) pair from a finished run to Langfuse.

    Each case becomes a fresh Langfuse trace with:
        - input  = {"question": ...}
        - output = actual_output text
        - metadata = {run_id, case_id, status, latency_ms, langsmith_run_id, ...}
    Each evaluator dimension becomes one ``score_trace`` call attached to
    that trace.

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

    def _push_one(res: dict[str, Any]) -> tuple[int, int, int]:
        """Run in a thread — the SDK is blocking. Returns (traces, scores, errors)."""
        traces = scores = errors = 0
        scores_dict: dict[str, float] = res.get("scores") or {}
        if not scores_dict:
            return (0, 0, 0)

        # Make sure score-configs exist before writing scores against them.
        for name in scores_dict.keys():
            _ensure_score_config_sync(client, name)

        # New trace per case. Trace id is opaque to Langfuse — pick a stable
        # form so re-syncs are idempotent (same case → same trace).
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
                    tags=["agent-eval", f"run:{run_id[:8]}"],
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
        return (traces, scores, errors)

    # Run pushes in parallel via the executor — Langfuse SDK is blocking, but
    # it batches under the hood and the network legs can overlap.
    results = await asyncio.gather(*[
        loop.run_in_executor(None, _push_one, r) for r in per_case_results
    ])
    for t, s, e in results:
        stats["traces"] += t
        stats["scores"] += s
        stats["errors"] += e

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
