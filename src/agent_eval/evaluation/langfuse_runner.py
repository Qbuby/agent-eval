"""Evaluation runner (post-PR3a: LangSmith-first, Langfuse optional).

Owns the lifecycle of a single eval run:
  1. Receive a list of normalized cases ({id, name, question, expected_output, metadata, source})
     produced by the API router from either a benchmark dataset or an uploaded file
  2. For each case: invoke the target SSE agent (LangGraph v2 protocol by default).
     We do NOT push any trace to Langfuse at runtime — the agent itself reports
     its own LangGraph trace to LangSmith.
  3. Run evaluator instances fetched from the DB (evaluator_configs table) over
     each (input, output, expected) tuple. Scores land in evaluation_scores.
  4. Aggregate cost/score summaries (split by pass/fail) → test_runs.summary_scores
  5. Fire-and-forget backfill task: query LangSmith by (project, start_time >=
     eval_started_at, inputs.messages[0].content == question) to find the root
     run id for each case. Writes test_results.langsmith_run_id when found.

Langfuse SDK import is retained so future work can re-enable remote write via
``settings.langfuse.remote_write=True`` without new dependencies; the live code
path below never calls dataset / score_trace unless that flag is set.
"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from langfuse import Langfuse  # kept for optional remote write; unused otherwise
from sqlalchemy import select

from agent_eval.config import settings
from agent_eval.db import async_session_factory
from agent_eval.db_models.repository import Repository
from agent_eval.db_models.tables import BenchmarkCaseRow
from agent_eval.evaluation.agent_adapter import (
    AgentResponse,
    OpenAICompatibleAdapter,
    SSEStreamAdapter,
)
from agent_eval.evaluation.configurable_judge import run_configurable_judge

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# Built-in evaluators
# ───────────────────────────────────────────────────────────────────────────


@dataclass
class EvaluatorResult:
    """One evaluator -> one or more named scores. value in [0.0, 1.0]."""
    scores: list[tuple[str, float, str]] = field(default_factory=list)  # (name, value, comment)


def _evaluator_exact_match(
    *, output: str, expected_output: str, params: dict, **_,
) -> EvaluatorResult:
    if not expected_output:
        return EvaluatorResult([("exact_match", 0.0, "no expected_output")])
    case_sensitive = bool(params.get("case_sensitive", False))
    a = output if case_sensitive else (output or "").lower()
    b = expected_output if case_sensitive else expected_output.lower()
    score = 1.0 if a.strip() == b.strip() else 0.0
    return EvaluatorResult([("exact_match", score, "")])


def _evaluator_tool_sequence(
    *, expected_tool_calls: list[dict] | None,
    actual_tool_calls: list[dict] | None,
    params: dict, **_,
) -> EvaluatorResult:
    """Compare expected tool names (in order) against actual.

    MVP semantics: ratio of (longest matching prefix length) / (max(expected, actual)).
    1.0 means full match; 0.0 means total miss. Doesn't compare arguments yet —
    that's Phase 2 (arg accuracy / hallucination).
    """
    expected = expected_tool_calls or []
    actual = actual_tool_calls or []
    if not expected:
        # No expectation set → treat as N/A and score 1.0 (don't penalize)
        return EvaluatorResult([("tool_sequence_match", 1.0, "no expected tools — pass-through")])
    exp_names = [e.get("tool_name") or e.get("name") or "" for e in expected]
    act_names = [a.get("tool_name") or a.get("name") or "" for a in actual]
    n = min(len(exp_names), len(act_names))
    matched = sum(1 for i in range(n) if exp_names[i] == act_names[i])
    denom = max(len(exp_names), len(act_names))
    score = matched / denom if denom else 0.0
    return EvaluatorResult([(
        "tool_sequence_match", score,
        f"matched {matched}/{denom} (expected={exp_names} actual={act_names})",
    )])


BUILTIN_EVALUATORS = {
    "exact_match": {
        "fn": _evaluator_exact_match, "is_async": False,
        "description": "字面完全相等返回 1，否则 0。可选 case_sensitive 参数。",
        "params_schema": {"case_sensitive": {"type": "boolean", "default": False}},
    },
    "tool_sequence_match": {
        "fn": _evaluator_tool_sequence, "is_async": False,
        "description": "对比 actual_tool_calls 与 expected_tool_calls 的工具名前缀匹配率。",
        "params_schema": {},
    },
}


# ───────────────────────────────────────────────────────────────────────────
# Cost aggregation
# ───────────────────────────────────────────────────────────────────────────


def _aggregate_cost(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute avg cost metrics over a list of dicts with keys:
    prompt_tokens, completion_tokens, total_tokens, tool_call_count,
    message_count, cache_creation_tokens, cache_read_tokens, latency_ms.
    Missing fields contribute None to the sample (skipped from avg)."""
    n = len(rows)
    if n == 0:
        return {"count": 0}

    def _avg(key: str) -> float | None:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    prompt = _avg("prompt_tokens")
    cache_read = _avg("cache_read_tokens")
    # cache_hit_rate = avg(cache_read) / avg(prompt_tokens).
    #
    # On LangChain Anthropic models, prompt_tokens already INCLUDES both
    # cache_creation and cache_read tokens (they are subcategories of
    # "input the model saw on this call"). So `prompt - cache_create` is
    # approximately `cache_read + non_cached`, which made the previous
    # formula `cache_read / (prompt - cache_create)` always come out near
    # 100%. The right denominator is just prompt_tokens — i.e. "of every
    # input token the model saw, how many came from cache?".
    cache_hit_rate = None
    if prompt is not None and prompt > 0 and cache_read is not None:
        cache_hit_rate = round((cache_read or 0) / prompt, 3)

    return {
        "count": n,
        "avg_prompt_tokens": prompt,
        "avg_completion_tokens": _avg("completion_tokens"),
        "avg_total_tokens": _avg("total_tokens"),
        "avg_tool_calls": _avg("tool_call_count"),
        "avg_messages": _avg("message_count"),
        "avg_latency_ms": _avg("latency_ms"),
        "avg_first_thinking_token_ms": _avg("first_thinking_token_ms"),
        "avg_first_answer_token_ms": _avg("first_answer_token_ms"),
        "cache_hit_rate": cache_hit_rate,
    }


# ───────────────────────────────────────────────────────────────────────────
# Run registry (in-process, NOT durable across server restarts)
# ───────────────────────────────────────────────────────────────────────────


@dataclass
class _RunHandle:
    run_id: str
    task: asyncio.Task
    cancel_event: asyncio.Event
    progress: dict[str, int] = field(default_factory=lambda: {"total": 0, "completed": 0, "failed": 0})


_RUN_REGISTRY: dict[str, _RunHandle] = {}


def get_run_progress(run_id: str) -> dict[str, int]:
    h = _RUN_REGISTRY.get(run_id)
    return dict(h.progress) if h else {}


def request_stop(run_id: str) -> bool:
    h = _RUN_REGISTRY.get(run_id)
    if h is None:
        return False
    h.cancel_event.set()
    return True


# ───────────────────────────────────────────────────────────────────────────
# Runner
# ───────────────────────────────────────────────────────────────────────────


def _make_adapter(
    agent_cfg: dict,
    *,
    thread_id: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> Any:
    """Build the HTTP adapter for one case.

    - ``type='sse'`` defaults to LangGraph v2 payload/event shape (the production
      agent used in D:/files/EPtestcases/agent_chat_sse_*.py). The caller passes
      a per-case thread_id so the agent receives a stable conversation handle
      (even though agent-side id rewriting may change what LangSmith records).
    - ``type='openai'`` for OpenAI-compatible /v1/chat/completions.
    - ``type='sse_generic'`` for the legacy templated SSE behaviour.

    ``client`` lets the caller inject one shared, connection-pooled
    ``httpx.AsyncClient`` for the whole run so 20 concurrent cases reuse
    keepalive connections (and one DNS lookup) instead of each opening a
    fresh client — the dominant cause of burst DNS failures / agent_unreachable.
    """
    t = agent_cfg.get("type", "sse")
    if t == "openai":
        return OpenAICompatibleAdapter(
            base_url=agent_cfg["url"],
            api_key=agent_cfg.get("api_key", ""),
            model=agent_cfg.get("model", "default"),
            timeout=float(agent_cfg.get("timeout", 120.0)),
            extra_headers=agent_cfg.get("headers") or None,
            client=client,
        )
    if t in ("sse", "sse_langgraph"):
        return SSEStreamAdapter(
            url=agent_cfg["url"],
            headers=agent_cfg.get("headers"),
            timeout=float(agent_cfg.get("timeout", 120.0)),
            mode="langgraph_v2",
            thread_id=thread_id,
            language=agent_cfg.get("language", "请用中文回复"),
            client=client,
        )
    if t == "sse_generic":
        return SSEStreamAdapter(
            url=agent_cfg["url"],
            headers=agent_cfg.get("headers"),
            payload_template=agent_cfg.get("payload_template"),
            timeout=float(agent_cfg.get("timeout", 120.0)),
            mode="generic",
            client=client,
        )
    raise ValueError(f"unknown agent type: {t!r}")


async def _resolve_judge_providers(specs: list[dict[str, Any]]) -> None:
    """Mutate ``specs`` in place: for any ``configurable_judge`` evaluator
    whose params reference a provider_id, fetch the EvaluatorProviderRow
    once and stash it under ``_provider``. Subsequent per-case scoring then
    reuses the row instead of round-tripping the DB on every sample.

    Resolution failures are logged but non-fatal — the case-level loop
    will detect ``_provider is None`` and skip the dimension instead of
    crashing the whole run.
    """
    needed: dict[str, list[dict[str, Any]]] = {}
    for spec in specs:
        if spec.get("evaluator_type") != "configurable_judge":
            continue
        pid = (spec.get("params") or {}).get("provider_id")
        if not pid:
            continue
        needed.setdefault(pid, []).append(spec)

    if not needed:
        return

    async with async_session_factory() as session:
        repo = Repository(session)
        for pid, specs_for_pid in needed.items():
            try:
                row = await repo.get_evaluator_provider(uuid.UUID(pid))
            except (TypeError, ValueError):
                row = None
            if row is None:
                logger.warning(
                    "configurable_judge: provider %s not found — affected evaluators "
                    "(%s) will be skipped at score time",
                    pid, ", ".join(s.get("label", "?") for s in specs_for_pid),
                )
            for s in specs_for_pid:
                s["_provider"] = row


def _langfuse_client() -> Langfuse:
    cfg = settings.langfuse
    if not cfg.configured:
        raise RuntimeError(
            "Langfuse not configured. Set LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / "
            "LANGFUSE_SECRET_KEY in .env."
        )
    return Langfuse(
        public_key=cfg.public_key,
        secret_key=cfg.secret_key,
        host=cfg.host,
    )


def _bench_case_to_dataset_input(case: BenchmarkCaseRow) -> dict[str, Any]:
    """Map a benchmark case → langfuse dataset input/expected/metadata triple.

    Default mapping is the simple Q→user-message form. For richer schemas the
    category.schema_config can override field names — out of MVP scope.
    """
    return {
        "input": {
            "messages": [{"role": "user", "content": case.question}],
            "question": case.question,
        },
        "expected_output": {
            "answer": case.reference_answer or "",
            "key_points": case.key_points or [],
        },
        "metadata": {
            "benchmark_case_id": str(case.id),
            "tags": list(case.tags or []),
            "difficulty": case.difficulty,
            "extra": case.extra_fields or {},
        },
    }


def _extract_tool_calls_from_response(resp: AgentResponse) -> list[dict]:
    """Best-effort extraction of tool calls from an OpenAI-style response.
    For SSE: not implemented in MVP — adapter doesn't surface them."""
    raw = getattr(resp, "raw_response", None)
    if not isinstance(raw, dict):
        return []
    try:
        msg = raw["choices"][0].get("message") or {}
        return msg.get("tool_calls") or []
    except (KeyError, IndexError, TypeError):
        return []


def _extract_usage(resp: AgentResponse) -> dict[str, int | None]:
    """Pull usage fields from raw_response if available (OpenAI / Anthropic style)."""
    raw = getattr(resp, "raw_response", None)
    out: dict[str, int | None] = {
        "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
        "cache_creation_tokens": None, "cache_read_tokens": None,
    }
    if isinstance(raw, dict):
        usage = raw.get("usage") or {}
        out["prompt_tokens"] = usage.get("prompt_tokens") or usage.get("input_tokens")
        out["completion_tokens"] = usage.get("completion_tokens") or usage.get("output_tokens")
        out["total_tokens"] = usage.get("total_tokens") or (
            (out["prompt_tokens"] or 0) + (out["completion_tokens"] or 0)
            if out["prompt_tokens"] is not None or out["completion_tokens"] is not None
            else None
        )
        # Anthropic-style cache fields
        details = usage.get("input_token_details") or usage.get("prompt_token_details") or {}
        if isinstance(details, dict):
            out["cache_creation_tokens"] = details.get("cache_creation")
            out["cache_read_tokens"] = details.get("cache_read")
    if out["total_tokens"] is None and resp.token_count is not None:
        out["total_tokens"] = resp.token_count
    return out


# ─── Agent invocation guards ──────────────────────────────────────────────
# The dev agent on host:18094 likes to fall asleep between runs and answer
# 502 to the very first call after wake-up. Without these guards, every
# eval started cold would land 3-N samples on "All connection attempts
# failed" and the user would think the platform was broken. Two layers:
#
#   1. _classify_agent_error: turn a raw exception into a stable category
#      (agent_unreachable / agent_timeout / agent_5xx / parse_error /
#      unknown) so the UI can render it differently than a plain "error".
#   2. _invoke_with_retry: configurable retry-with-exponential-backoff over
#      transient errors (connection / 5xx / timeout). Defaults to 2 retries
#      (3 total attempts) with 2s → 4s → 8s backoff capped at 30s. Three
#      sources of params, in priority order:
#         a) per-run override via agent_cfg["retry"]={max_retries,
#            initial_backoff_s, backoff_factor, max_backoff_s}
#         b) system_configs rows under category "eval.retry" (Config UI)
#         c) the hardcoded defaults in _RetryPolicy
#      Resolution happens once per run inside _execute_run via
#      _resolve_retry_policy, then the resolved policy is threaded through
#      to every _run_one_case so we don't hit the DB per sample. The
#      backoff sleep wakes early on a cancel_event so /stop doesn't have to
#      wait out a slow retry.

_TRANSIENT_HINTS = (
    "all connection attempts failed",
    "connection refused", "connection reset", "connection error",
    "bad gateway", "service unavailable", "gateway timeout",
    "502", "503", "504",
    "timed out", "timeout",
    # DNS resolution failures under burst concurrency inside containers.
    # httpx surfaces these as ConnectError with these substrings; they are
    # transient (the resolver recovers once the burst subsides), so they
    # MUST be retried — previously they matched no hint and failed terminally.
    "temporary failure in name resolution",
    "name or service not known",
    "failed to resolve",
    "nodename nor servname",
    "eai_again",
)


def _classify_agent_error(err: Exception) -> str:
    """Map a raised agent error to a stable category surfaced to the UI."""
    s = str(err).lower()
    if "502" in s or "bad gateway" in s or "503" in s or "service unavailable" in s:
        return "agent_unreachable"
    if "all connection attempts failed" in s or "connection refused" in s or "connection reset" in s:
        return "agent_unreachable"
    # DNS failures mean we never reached the agent — same bucket as a refused
    # connection so the UI renders it as infra (neutral), not a wrong answer.
    if (
        "temporary failure in name resolution" in s
        or "name or service not known" in s
        or "failed to resolve" in s
        or "nodename nor servname" in s
        or "eai_again" in s
    ):
        return "agent_unreachable"
    if "504" in s or "timed out" in s or "timeout" in s or "gateway timeout" in s:
        return "agent_timeout"
    if "5" in s and "client error" in s:
        return "agent_5xx"
    if "parse" in s or "json" in s or "decode" in s:
        return "parse_error"
    return "unknown"


def _is_transient(err: Exception) -> bool:
    s = str(err).lower()
    return any(hint in s for hint in _TRANSIENT_HINTS)


@dataclass
class _RetryPolicy:
    max_retries: int = 2
    initial_backoff_s: float = 2.0
    backoff_factor: float = 2.0
    max_backoff_s: float = 30.0


def _retry_policy_from_cfg(agent_cfg: dict) -> _RetryPolicy:
    raw = (agent_cfg or {}).get("retry") or {}
    try:
        return _RetryPolicy(
            max_retries=max(0, int(raw.get("max_retries", 2))),
            initial_backoff_s=max(0.0, float(raw.get("initial_backoff_s", 2.0))),
            backoff_factor=max(1.0, float(raw.get("backoff_factor", 2.0))),
            max_backoff_s=max(0.0, float(raw.get("max_backoff_s", 30.0))),
        )
    except (TypeError, ValueError):
        return _RetryPolicy()


async def _resolve_retry_policy(agent_cfg: dict) -> _RetryPolicy:
    """Three-tier fallback for retry params, resolved once per run:

      1. ``agent_cfg["retry"]`` — caller passed explicit overrides.
      2. ``system_configs`` rows under ``eval.retry.*`` (set via Config UI).
      3. Hardcoded defaults baked into ``_RetryPolicy``.

    The DB lookup is best-effort; any failure (table missing during tests,
    config_service not initialized) silently falls back to step 3 so the
    eval pipeline never blocks on configuration plumbing.
    """
    raw = dict((agent_cfg or {}).get("retry") or {})
    if not all(
        k in raw for k in
        ("max_retries", "initial_backoff_s", "backoff_factor", "max_backoff_s")
    ):
        try:
            from agent_eval.config_service import config_service
            for short_key, full_key in (
                ("max_retries", "eval.retry.max_retries"),
                ("initial_backoff_s", "eval.retry.initial_backoff_s"),
                ("backoff_factor", "eval.retry.backoff_factor"),
                ("max_backoff_s", "eval.retry.max_backoff_s"),
            ):
                if short_key in raw:
                    continue
                v = await config_service.get(full_key)
                if v is not None:
                    raw[short_key] = v
        except Exception as e:
            logger.debug("retry policy fallback to defaults: %s", e)
    return _retry_policy_from_cfg({"retry": raw})


async def _sleep_with_cancel(seconds: float, cancel_event: asyncio.Event | None) -> bool:
    """Sleep up to ``seconds`` but wake immediately if ``cancel_event`` fires.
    Returns True if cancelled, False if the full duration elapsed."""
    if seconds <= 0:
        return bool(cancel_event and cancel_event.is_set())
    if cancel_event is None:
        await asyncio.sleep(seconds)
        return False
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def _invoke_with_retry(
    adapter: Any,
    messages: list[dict],
    *,
    policy: _RetryPolicy,
    cancel_event: asyncio.Event | None = None,
) -> tuple[Any, int]:
    """Call ``adapter.invoke`` with up to ``policy.max_retries`` retries on
    transient errors. Returns ``(response, attempts_made)``; ``attempts_made``
    is 1-based (1 = succeeded on first try).

    Non-transient errors propagate immediately without retry. On terminal
    failure, the final exception is annotated with ``_eval_attempts_made``
    so the caller can record how many attempts were spent before giving up.
    If ``cancel_event`` fires during a backoff sleep, the loop aborts and
    the last seen exception propagates with the same annotation.
    """
    backoff = policy.initial_backoff_s
    total_attempts = policy.max_retries + 1
    for attempt in range(1, total_attempts + 1):
        try:
            resp = await adapter.invoke(messages)
            if attempt > 1:
                logger.info(
                    "agent invoke succeeded on attempt %d/%d",
                    attempt, total_attempts,
                )
            return resp, attempt
        except Exception as e:
            terminal = attempt >= total_attempts or not _is_transient(e)
            if terminal:
                setattr(e, "_eval_attempts_made", attempt)
                if attempt > 1:
                    logger.warning(
                        "agent invoke exhausted retries (%d/%d): %s",
                        attempt, total_attempts, type(e).__name__,
                    )
                raise
            logger.info(
                "agent transient err on attempt %d/%d (%s); backing off %.1fs",
                attempt, total_attempts, type(e).__name__, backoff,
            )
            # Jitter the sleep so 20 concurrent cases that all failed in the
            # same burst don't wake and retry in lockstep into the same
            # congestion window. Full jitter over [0, backoff].
            jittered = backoff * (0.5 + random.random() * 0.5)
            cancelled = await _sleep_with_cancel(jittered, cancel_event)
            if cancelled:
                setattr(e, "_eval_attempts_made", attempt)
                raise
            backoff = min(backoff * policy.backoff_factor, policy.max_backoff_s)
    # Unreachable: loop body always returns or raises.
    raise RuntimeError("retry loop exited without returning or raising")


async def _run_multiturn_case(
    *,
    case: dict[str, Any],
    agent_cfg: dict,
    evaluator_specs: list[dict[str, Any]],
    cancel_event: asyncio.Event | None = None,
    retry_policy: _RetryPolicy | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """多轮对话 case 执行：固定 thread_id 逐轮回放 + 逐轮/会话级打分。

    复用单轮的 adapter 工厂、重试器与 judge provider 预解析；回放与打分细节
    在 ``evaluation.multiturn``。返回 dict 与 ``_run_one_case`` 同契约，落库/
    聚合路径无需区分单轮多轮。"""
    from agent_eval.evaluation import multiturn

    input_messages = case.get("input_messages") or []
    conversation_goal = case.get("conversation_goal")
    turn_expectations = case.get("turn_expectations") or []
    question = case.get("question") or ""
    expected = case.get("expected_output") or ""
    agent_type = agent_cfg.get("type", "sse")

    # 整段对话共用一个 thread_id（agent 端按它维持上下文）。
    thread_id = f"eval-{case.get('name','conv')}-{uuid.uuid4().hex[:8]}"
    adapter = _make_adapter(agent_cfg, thread_id=thread_id, client=http_client)
    if retry_policy is None:
        retry_policy = _retry_policy_from_cfg(agent_cfg)

    async def _invoke(adp: Any, msgs: list[dict[str, Any]]):
        return await _invoke_with_retry(
            adp, msgs, policy=retry_policy, cancel_event=cancel_event,
        )

    invoked_at = datetime.now(timezone.utc)
    error_msg: str | None = None
    error_type: str | None = None
    replay: dict[str, Any] = {}
    scores: dict[str, float] = {}

    try:
        try:
            replay = await multiturn.replay_conversation(
                adapter=adapter,
                agent_type=agent_type,
                input_messages=input_messages,
                invoke=_invoke,
            )
        except Exception as e:
            attempts = getattr(e, "_eval_attempts_made", 1)
            error_msg = str(e)
            if attempts > 1:
                error_msg = f"{error_msg} (after {attempts} attempts)"
            error_type = _classify_agent_error(e)
            logger.warning(
                "multiturn replay failed for case %s [%s]: %s",
                case.get("id"), error_type, e,
            )
    finally:
        try:
            await adapter.close()
        except Exception:
            pass

    turns = replay.get("turns") or []
    # 整段输出快照：拼接 transcript，供列表/详情页展示与会话级回看。
    transcript = multiturn.build_transcript(turns)
    usage = replay.get("usage") or {
        "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
        "cache_creation_tokens": None, "cache_read_tokens": None,
    }
    actual_tool_calls = replay.get("tool_calls") or []
    cot_steps = replay.get("steps") or []

    # 打分（仅在回放成功时）。
    if not error_msg:
        try:
            scores = await multiturn.score_conversation(
                turns=turns,
                conversation_goal=conversation_goal,
                turn_expectations=turn_expectations,
                evaluator_specs=evaluator_specs,
                case_metadata=case.get("metadata"),
                case_id=case.get("id"),
            )
        except Exception as e:
            logger.warning("multiturn scoring crashed on case %s: %s", case.get("id"), e)

    status = "pass"
    if error_msg:
        if error_type in ("agent_unreachable", "agent_timeout"):
            status = error_type
        else:
            status = "error"
    elif scores and any(v < 0.5 for v in scores.values()):
        status = "fail"

    return {
        "case_id": case.get("id"),
        "case_name": case.get("name"),
        "case_source": case.get("source"),
        "thread_id": thread_id,
        "question": question,
        "expected_output": expected,
        "expected_tool_calls": [],
        "invoked_at": invoked_at,
        "status": status,
        "actual_output": transcript,
        "actual_tool_calls": actual_tool_calls,
        "cot_steps": cot_steps,
        # 多轮专属：完整逐轮记录 + 会话级上下文，落库进 full_trace.conversation。
        "conversation": {
            "turns": turns,
            "goal": conversation_goal,
            "turn_expectations": turn_expectations,
        },
        "latency_ms": replay.get("latency_ms"),
        "first_thinking_token_ms": None,
        "first_answer_token_ms": None,
        "error_message": error_msg,
        "error_type": error_type,
        "attempts_made": replay.get("attempts", 1),
        "tool_call_count": len(actual_tool_calls),
        "message_count": len(turns),
        "scores": scores,
        **usage,
    }


async def _run_one_case(
    *,
    case: dict[str, Any],
    agent_cfg: dict,
    evaluator_specs: list[dict[str, Any]],
    cancel_event: asyncio.Event | None = None,
    retry_policy: _RetryPolicy | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Execute one case end-to-end. ``case`` is the normalized dict from
    ``_normalize_cases_for_runner``: {id, name, question, expected_output,
    expected_tool_calls, source}. ``evaluator_specs`` are pre-resolved
    {evaluator_type, params, label} dicts (DB lookup already done by caller).
    ``retry_policy`` is normally resolved once at run start (via
    ``_resolve_retry_policy``) and threaded through; passing ``None`` falls
    back to per-call resolution from ``agent_cfg`` only (no DB lookup) for
    callers/tests that don't go through ``_execute_run``.
    Returns one row's worth of data ready to persist."""
    # 多轮对话样例（source='conversation'）：走 multiturn 回放+逐轮/会话级打分。
    # 返回 dict 与单轮契约一致，落库/聚合无需区分。
    if case.get("multi_turn"):
        return await _run_multiturn_case(
            case=case,
            agent_cfg=agent_cfg,
            evaluator_specs=evaluator_specs,
            cancel_event=cancel_event,
            retry_policy=retry_policy,
            http_client=http_client,
        )

    question = case["question"]
    expected = case.get("expected_output") or ""
    expected_tool_calls = case.get("expected_tool_calls") or []
    # We send a thread_id the agent CAN consume; the agent may rewrite it on
    # its side (we observed this against ep-agent / ruyi-agent), so we never
    # treat thread_id as the LangSmith join key — the post-run backfill uses
    # (start_time, question text) instead.
    thread_id = f"eval-{case.get('name','case')}-{uuid.uuid4().hex[:8]}"

    adapter = _make_adapter(agent_cfg, thread_id=thread_id, client=http_client)
    messages = [{"role": "user", "content": question}]
    if retry_policy is None:
        retry_policy = _retry_policy_from_cfg(agent_cfg)

    output_text = ""
    error_msg: str | None = None
    error_type: str | None = None
    actual_tool_calls: list[dict] = []
    cot_steps: list[dict] = []
    latency_ms: int | None = None
    first_thinking_token_ms: int | None = None
    first_answer_token_ms: int | None = None
    attempts_made = 0
    usage = {
        "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
        "cache_creation_tokens": None, "cache_read_tokens": None,
    }
    invoked_at = datetime.now(timezone.utc)

    try:
        try:
            resp, attempts_made = await _invoke_with_retry(
                adapter, messages,
                policy=retry_policy, cancel_event=cancel_event,
            )
            output_text = resp.content
            latency_ms = int(resp.latency_ms)
            usage = _extract_usage(resp)
            # SSE/LangGraph adapter packs tool_calls into raw_response
            raw = getattr(resp, "raw_response", None)
            if isinstance(raw, dict):
                tcs = raw.get("tool_calls")
                if isinstance(tcs, list):
                    actual_tool_calls = tcs
                steps_raw = raw.get("steps")
                if isinstance(steps_raw, list):
                    cot_steps = steps_raw
            if not actual_tool_calls:
                actual_tool_calls = _extract_tool_calls_from_response(resp)
            # Time-to-first-token, derived from per-step first_token_ms that
            # SSEStreamAdapter stamps on each thought/answer step.
            #   thinking = first thought-or-answer step that has a value
            #   answer   = the step whose type='answer' (final LLM step)
            for s in cot_steps:
                if not isinstance(s, dict):
                    continue
                t = s.get("first_token_ms")
                if t is None:
                    continue
                if s.get("type") in ("thought", "answer") and first_thinking_token_ms is None:
                    first_thinking_token_ms = int(t)
                if s.get("type") == "answer":
                    first_answer_token_ms = int(t)
        except Exception as e:
            attempts_made = getattr(e, "_eval_attempts_made", attempts_made or 1)
            error_msg = str(e)
            if attempts_made > 1:
                error_msg = f"{error_msg} (after {attempts_made} attempts)"
            error_type = _classify_agent_error(e)
            logger.warning(
                "agent invoke failed for case %s [%s] after %d attempt(s): %s",
                case.get("id"), error_type, attempts_made, e,
            )
    finally:
        try:
            await adapter.close()
        except Exception:
            pass

    # ── Run evaluators (no Langfuse server write; scores stay in local DB) ──
    scores: dict[str, float] = {}
    if not error_msg:
        for spec in evaluator_specs:
            etype = spec.get("evaluator_type")
            label = spec.get("label") or etype or "evaluator"
            # In tag-only mode (post-2026-05-19) evaluators don't define a
            # local scoring function — they're just template tags forwarded
            # to Langfuse. Skip the local-scoring loop for them; their
            # contribution shows up later via the Langfuse pull-back.
            if not etype:
                continue
            # Configurable LLM judge — params (provider/model/prompt/dims)
            # are saved on the evaluator row; provider was pre-resolved into
            # spec['_provider'] by _execute_run.
            if etype == "configurable_judge":
                provider_row = spec.get("_provider")
                if provider_row is None:
                    logger.warning(
                        "configurable_judge[%s]: skipped (no provider resolved) on case %s",
                        label, case.get("id"),
                    )
                    continue
                try:
                    judge_result = await run_configurable_judge(
                        params=spec.get("params") or {},
                        provider=provider_row,
                        input_text=question,
                        output_text=output_text,
                        expected_output=expected,
                        metadata=case.get("metadata"),
                        evaluator_name=label,
                    )
                except Exception as e:
                    logger.warning(
                        "configurable_judge[%s] crashed on case %s: %s",
                        label, case.get("id"), e,
                    )
                    continue
                if judge_result.error and not judge_result.scores:
                    logger.warning(
                        "configurable_judge[%s] error on case %s: %s",
                        label, case.get("id"), judge_result.error,
                    )
                    continue
                # Single-score paradigm: configurable_judge produces at most
                # one JudgeScore per evaluator; we use evaluator label as the
                # score key so multiple instances don't collide.
                for s in judge_result.scores:
                    scores[label] = float(s.value)
                continue

            ev_def = BUILTIN_EVALUATORS.get(etype)
            if ev_def is None:
                # Unknown legacy type — silently skip so old runs don't
                # crash the pipeline.
                continue
            fn = ev_def["fn"]
            kwargs = {
                "input": question, "output": output_text,
                "expected_output": expected,
                "expected_tool_calls": expected_tool_calls,
                "actual_tool_calls": actual_tool_calls,
                "params": spec.get("params") or {},
            }
            try:
                result = await fn(**kwargs) if ev_def["is_async"] else fn(**kwargs)
                for score_name, value, comment in result.scores:
                    # Prefix with evaluator instance label so multiple instances
                    # of the same type don't collide.
                    full = f"{label}.{score_name}" if label != score_name else score_name
                    scores[full] = float(value)
            except Exception as e:
                logger.warning("evaluator %s crashed on case %s: %s", label, case.get("id"), e)

    # status: pass if all evaluator scores >= 0.5 and no error
    status = "pass"
    if error_msg:
        # Surface infrastructure failures separately from "agent answered
        # but answered wrong". UI then renders agent_unreachable as a
        # neutral grey instead of red, and the run summary can flag it.
        if error_type in ("agent_unreachable", "agent_timeout"):
            status = error_type
        else:
            status = "error"
    elif scores and any(v < 0.5 for v in scores.values()):
        status = "fail"

    return {
        "case_id": case.get("id"),
        "case_name": case.get("name"),
        "case_source": case.get("source"),
        "thread_id": thread_id,
        "question": question,
        "expected_output": expected,
        "expected_tool_calls": expected_tool_calls,
        "invoked_at": invoked_at,
        "status": status,
        "actual_output": output_text,
        "actual_tool_calls": actual_tool_calls,
        "cot_steps": cot_steps,
        "latency_ms": latency_ms,
        "first_thinking_token_ms": first_thinking_token_ms,
        "first_answer_token_ms": first_answer_token_ms,
        "error_message": error_msg,
        "error_type": error_type,
        "attempts_made": attempts_made,
        "tool_call_count": len(actual_tool_calls),
        "message_count": len(messages),
        "scores": scores,
        **usage,
    }


async def _execute_run(
    run_id: str,
    cases: list[dict[str, Any]],
    agent_cfg: dict,
    evaluator_specs: list[dict[str, Any]],
    concurrency: int,
    run_name: str,
    langsmith_project: str | None,
    cancel_event: asyncio.Event,
    handle: _RunHandle,
) -> None:
    """Background task body. Invokes agent for each case, runs evaluators,
    persists results. After all cases settle, optionally kicks off the
    LangSmith backfill (non-blocking)."""
    handle.progress["total"] = len(cases)
    retry_policy = await _resolve_retry_policy(agent_cfg)
    logger.info(
        "eval run %s retry policy: max_retries=%d initial=%.1fs factor=%.1f cap=%.1fs",
        run_id, retry_policy.max_retries, retry_policy.initial_backoff_s,
        retry_policy.backoff_factor, retry_policy.max_backoff_s,
    )

    # Pre-resolve EvaluatorProviderRow for any configurable_judge specs so
    # _run_one_case doesn't hit the DB per case. Each spec gets a `_provider`
    # field — runtime cache only, never persisted.
    await _resolve_judge_providers(evaluator_specs)

    sem = asyncio.Semaphore(max(1, concurrency))
    per_case_results: list[dict[str, Any]] = []

    # One shared, connection-pooled client for the whole run. Without this each
    # case opened its own httpx.AsyncClient with an unbounded pool, so 20
    # concurrent cases meant 20 independent connection pools and a burst of
    # simultaneous DNS lookups — the container's resolver drops these under
    # load ("Temporary failure in name resolution"), surfacing as spurious
    # agent_unreachable. Capping connections at the concurrency level and
    # reusing keepalive connections removes both the DNS burst and the
    # connection churn. keepalive_expiry is generous because agent calls can
    # take tens of seconds (multi-step LangGraph) between requests on a slot.
    limits = httpx.Limits(
        max_connections=max(1, concurrency),
        max_keepalive_connections=max(1, concurrency),
        keepalive_expiry=120.0,
    )
    agent_timeout = float(agent_cfg.get("timeout", 120.0))
    http_client = httpx.AsyncClient(limits=limits, timeout=agent_timeout)

    async def _do_one(case: dict[str, Any]):
        if cancel_event.is_set():
            return
        async with sem:
            if cancel_event.is_set():
                return
            try:
                res = await _run_one_case(
                    case=case,
                    agent_cfg=agent_cfg,
                    evaluator_specs=evaluator_specs,
                    cancel_event=cancel_event,
                    retry_policy=retry_policy,
                    http_client=http_client,
                )
            except Exception as e:
                logger.exception("case %s crashed during run: %s", case.get("id"), e)
                handle.progress["failed"] += 1
                handle.progress["completed"] += 1
                return
            per_case_results.append(res)
            if res["status"] == "error":
                handle.progress["failed"] += 1
            handle.progress["completed"] += 1

            # Persist per-case row
            try:
                async with async_session_factory() as session:
                    repo = Repository(session)
                    # Accept either benchmark_case_id (when case.source='benchmark')
                    # or leave both nullable (when case.source='file').
                    src = case.get("source")
                    bench_id = None
                    if src == "benchmark" and case.get("id"):
                        try:
                            bench_id = uuid.UUID(case["id"])
                        except (ValueError, TypeError):
                            bench_id = None
                    # full_trace 承载单轮 steps；多轮额外带 conversation
                    # （逐轮记录 + goal + turn_expectations），供详情页按轮回看。
                    full_trace: dict[str, Any] | None = None
                    if res.get("cot_steps") or res.get("conversation"):
                        full_trace = {}
                        if res.get("cot_steps"):
                            full_trace["steps"] = res["cot_steps"]
                        if res.get("conversation"):
                            full_trace["conversation"] = res["conversation"]
                    created = await repo.create_test_result(
                        uuid.UUID(run_id),
                        benchmark_case_id=bench_id,
                        question=res["question"],
                        expected_output=res.get("expected_output") or None,
                        thread_id=res["thread_id"],
                        actual_output=res["actual_output"],
                        actual_tool_calls=res["actual_tool_calls"] or None,
                        full_trace=full_trace,
                        latency_ms=res["latency_ms"],
                        total_tokens=res["total_tokens"],
                        prompt_tokens=res["prompt_tokens"],
                        completion_tokens=res["completion_tokens"],
                        cache_creation_tokens=res.get("cache_creation_tokens"),
                        cache_read_tokens=res.get("cache_read_tokens"),
                        tool_call_count=res["tool_call_count"],
                        first_thinking_token_ms=res.get("first_thinking_token_ms"),
                        first_answer_token_ms=res.get("first_answer_token_ms"),
                        error_message=res["error_message"],
                        error_type=res["error_type"],
                        status=res["status"],
                        attempts_made=res.get("attempts_made", 1),
                    )
                    for sname, sval in res["scores"].items():
                        await repo.create_eval_score(
                            created.id, dimension=sname, score=sval,
                            weight=1.0, weighted_score=sval, scoring_method="eval",
                            details={},
                        )
                    await session.commit()
            except Exception as e:
                logger.exception("failed to persist results for case %s: %s", case.get("id"), e)

    try:
        await asyncio.gather(*[_do_one(c) for c in cases])
    finally:
        # Close the shared client before aggregation so its connections are
        # released even if aggregation/backfill below raises.
        try:
            await http_client.aclose()
        except Exception:
            pass
        # Aggregate
        succ = [r for r in per_case_results if r["status"] == "pass"]
        fail = [r for r in per_case_results if r["status"] != "pass"]
        all_scores: dict[str, list[float]] = {}
        for r in per_case_results:
            for k, v in r["scores"].items():
                all_scores.setdefault(k, []).append(v)
        dim_avg = {k: round(sum(vs) / len(vs), 3) for k, vs in all_scores.items() if vs}

        # Per-dimension histogram in fixed buckets so the UI can render a
        # quick distribution chart without re-aggregating in the browser.
        # Buckets are half-open intervals on [0,1].
        bucket_edges = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0001]
        bucket_labels = ["0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1"]
        score_distribution: dict[str, list[int]] = {}
        for dim, vs in all_scores.items():
            counts = [0] * (len(bucket_edges) - 1)
            for v in vs:
                for i in range(len(counts)):
                    if bucket_edges[i] <= v < bucket_edges[i + 1]:
                        counts[i] += 1
                        break
            score_distribution[dim] = counts

        # Tool usage: per-tool aggregate over ALL cases (success + failure).
        # Each entry of per_case_results['actual_tool_calls'] is a list of
        # {tool_name, args, output} dicts. We count invocations and flag
        # likely failures by looking for an "error" key or non-empty
        # error string in the output.
        tool_stats: dict[str, dict[str, Any]] = {}
        for r in per_case_results:
            for call in (r.get("actual_tool_calls") or []):
                if not isinstance(call, dict):
                    continue
                name = call.get("tool_name") or call.get("name") or "unknown"
                slot = tool_stats.setdefault(name, {
                    "name": name, "calls": 0, "errors": 0, "cases": 0,
                })
                slot["calls"] += 1
                out = call.get("output")
                if isinstance(out, dict) and (out.get("error") or out.get("isError")):
                    slot["errors"] += 1
                elif isinstance(out, str) and out.lower().startswith("error"):
                    slot["errors"] += 1
            seen_in_case = {
                (c.get("tool_name") or c.get("name") or "unknown")
                for c in (r.get("actual_tool_calls") or [])
                if isinstance(c, dict)
            }
            for nm in seen_in_case:
                tool_stats.setdefault(nm, {"name": nm, "calls": 0, "errors": 0, "cases": 0})
                tool_stats[nm]["cases"] += 1
        tool_usage = sorted(
            tool_stats.values(),
            key=lambda x: (-x["calls"], x["name"]),
        )

        attempts_list = [int(r.get("attempts_made") or 1) for r in per_case_results]
        retried = [n for n in attempts_list if n > 1]
        retry_stats: dict[str, Any] = {
            "total_cases": len(attempts_list),
            "cases_with_retries": len(retried),
            "max_attempts": max(attempts_list) if attempts_list else 0,
            "avg_attempts": round(
                sum(attempts_list) / len(attempts_list), 3
            ) if attempts_list else 0.0,
            "total_retries": sum(n - 1 for n in attempts_list),
        }

        summary: dict[str, Any] = {
            "counts": {
                "total": len(per_case_results),
                "passed": len(succ),
                "failed": len(fail),
                "unreachable": sum(
                    1 for r in per_case_results
                    if r["status"] in ("agent_unreachable", "agent_timeout")
                ),
            },
            "dimension_averages": dim_avg,
            "score_distribution": {
                "buckets": bucket_labels,
                "by_dimension": score_distribution,
            },
            "tool_usage": tool_usage,
            "cost_success": _aggregate_cost(succ),
            "cost_failure": _aggregate_cost(fail),
            "retry_stats": retry_stats,
            "run_name": run_name,
        }
        # If most samples couldn't reach the agent, surface that on the run
        # so the detail page can render a banner instead of leaving the user
        # to scan 30 rows of "All connection attempts failed".
        if per_case_results:
            unreach_ratio = summary["counts"]["unreachable"] / len(per_case_results)
            if unreach_ratio >= 0.5:
                agent_url = (agent_cfg or {}).get("url", "")
                summary["runtime_error"] = (
                    f"{summary['counts']['unreachable']}/{len(per_case_results)} 样例无法连到被测 agent "
                    f"({agent_url})。请确认 agent 服务在线、网络可达；如在容器内访问宿主机请用 host.docker.internal "
                    "或宿主机 LAN IP 而非 localhost。"
                )
        if langsmith_project:
            summary["langsmith_project"] = langsmith_project
            # LangSmith web root; the UI uses it to deep-link.
            summary["langsmith_host"] = settings.langsmith.api_url.replace(
                "api.smith", "smith"
            ) if settings.langsmith.api_url else None
        if settings.langfuse.remote_write and settings.langfuse.configured:
            summary["langfuse_host"] = settings.langfuse.host
        if cancel_event.is_set():
            summary["stopped_early"] = True

        async with async_session_factory() as session:
            repo = Repository(session)
            status = "completed"
            if cancel_event.is_set():
                status = "interrupted"
            elif handle.progress["completed"] == 0:
                status = "failed"
            await repo.finish_test_run(uuid.UUID(run_id), summary, status=status)
            await session.commit()

        # Fire-and-forget LangSmith backfill. Don't block the run's completion
        # on slow LangSmith queries — users can refresh the detail page and
        # gradually see langsmith_run_id fill in.
        if langsmith_project and per_case_results:
            asyncio.create_task(
                _backfill_langsmith_traces(
                    run_id=run_id,
                    project=langsmith_project,
                    per_case_results=per_case_results,
                )
            )

        # Fire-and-forget Langfuse score sync. Off by default — flip
        # LANGFUSE_REMOTE_WRITE=true (or set langfuse.remote_write in /config)
        # to push every evaluator score into the Langfuse UI as a fresh trace
        # per case. Doesn't depend on LangSmith.
        if settings.langfuse.remote_write and settings.langfuse.configured and per_case_results:
            from agent_eval.evaluation.langfuse_sync import (
                pull_evaluator_scores_for_run, sync_run_scores_to_langfuse,
            )

            async def _sync_then_pull():
                # Step 1: push our local scores + traces to Langfuse, persist
                # the new langfuse_trace_id back to test_results. Also stamp
                # each selected evaluator's tag onto every trace so Langfuse
                # evaluators bound to those tags pick the trace up.
                eval_tags = [
                    spec.get("tag") or spec.get("label")
                    for spec in evaluator_specs
                    if spec.get("tag") or spec.get("label")
                ]
                # de-dup while preserving order
                seen: set[str] = set()
                eval_tags = [t for t in eval_tags if t not in seen and not seen.add(t)]
                await sync_run_scores_to_langfuse(
                    run_id=run_id,
                    run_name=run_name,
                    per_case_results=per_case_results,
                    extra_tags=eval_tags,
                )
                # Step 2: poll Langfuse for evaluator-produced scores and
                # stamp them back into evaluation_scores. Worker latency is
                # 1-5min, so we poll up to 5 minutes total.
                await pull_evaluator_scores_for_run(run_id=run_id)

            asyncio.create_task(_sync_then_pull())

        _RUN_REGISTRY.pop(run_id, None)


async def _backfill_langsmith_traces(
    *,
    run_id: str,
    project: str,
    per_case_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Try to match each local result with a LangSmith root run by
    (start_time >= invoked_at - 1min) AND inputs.messages[0].content == question.

    One LangSmith query per case keeps the call pattern simple.

    Returns a small diagnostics dict so the API layer can surface *why* zero
    matched. Previously this function silently swallowed every exception and
    returned None, which made permission errors look identical to "the
    project is correct, just no run yet". The new shape:

        {
          "errors": int,            # how many per-case searches raised
          "error_kind": str | None, # one of: forbidden, unauthorized,
                                    # network, client_init, unknown, None
          "error_message": str | None,  # first error string, truncated
        }
    """
    diagnostics: dict[str, Any] = {
        "errors": 0,
        "error_kind": None,
        "error_message": None,
    }

    try:
        from langsmith import Client
        # Same precedence as TraceExtractor uses (see api/dependencies.py):
        # config_service (DB-backed, set via /config UI) wins over .env.
        # Without this, the eval backfill silently used the stale .env key
        # while the Traces page used the working DB key — same project,
        # different verdict. Fixed by going through the shared helper.
        from agent_eval.api.dependencies import _get_langsmith_kwargs
        kwargs = await _get_langsmith_kwargs()
        if not kwargs.get("api_key"):
            logger.info("backfill: no LangSmith API key configured (DB or .env), skipping")
            diagnostics["error_kind"] = "client_init"
            diagnostics["error_message"] = "LangSmith API key not configured (set via /config or LANGSMITH_API_KEY)"
            return diagnostics
        client = Client(**kwargs)
    except Exception as e:
        logger.warning("backfill: langsmith client init failed: %s", e)
        diagnostics["error_kind"] = "client_init"
        diagnostics["error_message"] = str(e)[:300]
        return diagnostics

    # ── Build a window covering all cases ─────────────────────────────────
    # Previous version did one client.list_runs per case (each pulling 50
    # rows). With a busy ruyi-agent project that's 10 cases × 5-10s = a
    # full minute of LangSmith traffic per backfill — clients time out and
    # the work gets retried over and over. Replace with a single window
    # query covering the earliest case to the latest, then hash by
    # question text locally.
    loop = asyncio.get_event_loop()
    from datetime import timedelta
    invoked_times = [r["invoked_at"] for r in per_case_results if r.get("invoked_at")]
    if not invoked_times:
        return diagnostics
    window_lower = min(invoked_times) - timedelta(minutes=1)
    window_upper = max(invoked_times) + timedelta(minutes=10)

    # Bounded fetch — LangSmith caps list_runs at limit=100. If the project
    # is so busy this isn't enough, the user can narrow the time window by
    # re-running with fewer cases (or paginate later if real demand shows).
    PAGE_LIMIT = 100
    by_question: dict[str, str] = {}  # question text → run id

    def _fetch_window() -> Exception | None:
        try:
            runs = client.list_runs(
                project_name=project,
                is_root=True,
                start_time=window_lower,
                end_time=window_upper,
                limit=PAGE_LIMIT,
            )
            for r in runs:
                inp = getattr(r, "inputs", None)
                if not isinstance(inp, dict):
                    continue
                msgs = inp.get("messages")
                if isinstance(msgs, list) and msgs:
                    last = msgs[-1] if isinstance(msgs[-1], dict) else None
                    txt = (last or {}).get("content", "")
                else:
                    txt = inp.get("question") or ""
                if isinstance(txt, str) and txt:
                    # Latest wins on duplicate questions — but the time
                    # window already prunes most dupes, so this is rare.
                    by_question.setdefault(txt, str(r.id))
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("backfill window fetch err: %s", e)
            return e

    err = await loop.run_in_executor(None, _fetch_window)
    if err is not None:
        diagnostics["errors"] = len(per_case_results)
        diagnostics["error_kind"] = _classify_langsmith_error(err)
        diagnostics["error_message"] = str(err)[:300]
        return diagnostics

    logger.info(
        "backfill: project=%s window=%s..%s fetched=%d unique-questions",
        project, window_lower.isoformat(), window_upper.isoformat(),
        len(by_question),
    )

    # ── Match local cases against the in-memory map ───────────────────────
    triples: list[tuple[str, str | None]] = []
    for res in per_case_results:
        thread_id = res.get("thread_id") or ""
        question = res.get("question") or ""
        triples.append((thread_id, by_question.get(question)))

    hits = 0
    async with async_session_factory() as session:
        from agent_eval.db_models.tables import TestResultRow
        for thread_id, lsrun in triples:
            if not lsrun or not thread_id:
                continue
            rows = (await session.execute(
                select(TestResultRow)
                .where(TestResultRow.run_id == uuid.UUID(run_id))
                .where(TestResultRow.thread_id == thread_id)
            )).scalars().all()
            for row in rows:
                row.langsmith_run_id = lsrun
                hits += 1
        await session.commit()
    logger.info(
        "backfill: project=%s run=%s matched %d/%d cases (errors=%d kind=%s)",
        project, run_id, hits, len(per_case_results),
        diagnostics["errors"], diagnostics["error_kind"],
    )
    return diagnostics


def _classify_langsmith_error(err: Exception) -> str:
    """Map a raised LangSmith client error to a stable banner category.

    The langsmith-sdk wraps httpx errors in its own LangSmithError subclasses;
    we sniff the stringified form rather than depend on private types.
    """
    s = str(err).lower()
    if "403" in s or "forbidden" in s:
        return "forbidden"
    if "401" in s or "unauthorized" in s:
        return "unauthorized"
    if "404" in s or "not found" in s:
        return "not_found"
    if any(t in s for t in ("connection", "timeout", "timed out", "dns")):
        return "network"
    return "unknown"


def _run_matches_question(run_obj: Any, question: str) -> bool:
    """Return True if ``run_obj.inputs`` carries the same user question.

    Accepts either ``inputs.messages[-1].content`` (LangChain ChatModel
    convention) or ``inputs.question`` (plain dict). Tolerant to missing
    fields — never raises.
    """
    inp = getattr(run_obj, "inputs", None)
    if not isinstance(inp, dict):
        return False
    msgs = inp.get("messages")
    if isinstance(msgs, list) and msgs:
        last = msgs[-1] if isinstance(msgs[-1], dict) else None
        txt = (last or {}).get("content", "")
    else:
        txt = inp.get("question") or ""
    return isinstance(txt, str) and txt == question


async def rerun_backfill(*, run_id: str, project: str) -> dict[str, Any]:
    """Re-execute the LangSmith trace backfill for an existing run.

    Triggered from the detail page when the user supplies a project name —
    they may want to point at a different LangSmith project than the one
    bound at start time, or retry after fixing API permissions.

    Rebuilds the in-memory case list from ``test_results``: each row already
    carries ``thread_id`` (DB join key), ``question`` (LangSmith match key)
    and ``invoked_at`` (time-window lower bound, derived from started_at /
    created_at). Rows missing question or invoked_at are skipped.

    Also persists ``project`` to ``test_runs.langsmith_project`` so the row
    detail page picks up the new value on subsequent loads.

    Returns ``{"matched": int, "scanned": int, "errors": int,
    "error_kind": str | None, "error_message": str | None}``. ``error_kind``
    is the stable category surfaced to the frontend banner so users can tell
    "API key forbidden on this project" apart from "0 matches, project is
    fine but no run yet".
    """
    from agent_eval.db_models.tables import TestResultRow, TestRunRow

    async with async_session_factory() as session:
        run_row = (await session.execute(
            select(TestRunRow).where(TestRunRow.id == uuid.UUID(run_id))
        )).scalar_one_or_none()
        if run_row is None:
            raise ValueError(f"run not found: {run_id}")
        results = (await session.execute(
            select(TestResultRow).where(TestResultRow.run_id == uuid.UUID(run_id))
        )).scalars().all()

        per_case_results: list[dict[str, Any]] = []
        fallback_invoked_at = run_row.eval_started_at or run_row.started_at or run_row.created_at
        for r in results:
            if not r.question:
                continue
            invoked_at = r.created_at or fallback_invoked_at
            if invoked_at is None:
                continue
            per_case_results.append({
                "thread_id": r.thread_id or "",
                "question": r.question,
                "invoked_at": invoked_at,
            })

        # Persist the project switch so the detail page reads it back.
        run_row.langsmith_project = project
        await session.commit()

    if not per_case_results:
        return {
            "matched": 0, "scanned": 0,
            "errors": 0, "error_kind": None, "error_message": None,
        }

    diagnostics = await _backfill_langsmith_traces(
        run_id=run_id, project=project, per_case_results=per_case_results,
    )

    # Count hits by re-reading the rows.
    async with async_session_factory() as session:
        hit_rows = (await session.execute(
            select(TestResultRow)
            .where(TestResultRow.run_id == uuid.UUID(run_id))
            .where(TestResultRow.langsmith_run_id.isnot(None))
        )).scalars().all()
    return {
        "matched": len(hit_rows),
        "scanned": len(per_case_results),
        "errors": diagnostics["errors"],
        "error_kind": diagnostics["error_kind"],
        "error_message": diagnostics["error_message"],
    }


# ───────────────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────────────


async def start_run(
    *,
    cases: list[dict[str, Any]],
    agent_cfg: dict,
    evaluator_specs: list[dict[str, Any]],
    concurrency: int = 3,
    run_name: str | None = None,
    langsmith_project: str | None = None,
    benchmark_version_id: str | None = None,
    eval_case_source_id: str | None = None,
) -> str:
    """Create a test_runs row, register an asyncio task, return run_id.

    ``cases`` is the pre-normalized list from the API router:
        [{"id": str, "name": str, "question": str, "expected_output": str,
          "expected_tool_calls": list, "metadata": dict, "source": "benchmark"|"file"}, ...]

    ``evaluator_specs`` is a list of DB-resolved evaluator configs:
        [{"evaluator_type": "configurable_judge", "params": {...}, "label": "my-judge"}, ...]
    """
    if not cases:
        raise ValueError("no cases selected")
    if not evaluator_specs:
        raise ValueError("at least one evaluator required")

    run_name = run_name or f"eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.create_test_run(
            benchmark_version_id=uuid.UUID(benchmark_version_id) if benchmark_version_id else None,
            eval_case_source_id=uuid.UUID(eval_case_source_id) if eval_case_source_id else None,
            agent_config=agent_cfg,
            langfuse_run_name=run_name,
            langsmith_project=langsmith_project,
            evaluator_configs=evaluator_specs,
            status="running",
        )
        await session.commit()
        run_id = str(row.id)

    cancel_event = asyncio.Event()
    handle = _RunHandle(run_id=run_id, task=None, cancel_event=cancel_event)  # type: ignore[arg-type]
    handle.task = asyncio.create_task(_execute_run(
        run_id=run_id,
        cases=cases,
        agent_cfg=agent_cfg,
        evaluator_specs=evaluator_specs,
        concurrency=concurrency,
        run_name=run_name,
        langsmith_project=langsmith_project,
        cancel_event=cancel_event,
        handle=handle,
    ))
    _RUN_REGISTRY[run_id] = handle
    return run_id


async def sweep_orphaned_runs() -> int:
    """On startup: any test_runs.status='running' from a previous process is dead.
    Mark them 'interrupted' so the UI doesn't show stale spinners forever."""
    from agent_eval.db_models.tables import TestRunRow
    async with async_session_factory() as session:
        rows = (await session.execute(
            select(TestRunRow).where(TestRunRow.status == "running")
        )).scalars().all()
        n = 0
        for row in rows:
            if str(row.id) in _RUN_REGISTRY:
                continue  # actually live in this process
            row.status = "interrupted"
            row.finished_at = datetime.now(timezone.utc)
            n += 1
        await session.commit()
        return n
