"""Langfuse-backed evaluation runner.

Owns the lifecycle of a single eval run:
  1. Convert a (subset of) BenchmarkCases → Langfuse dataset items (idempotent)
  2. For each item: invoke target agent (HTTP/SSE), wrap the call in a Langfuse
     generation span so cost / tool-calls land in the trace
  3. Run built-in evaluators over each (input, output, expected) tuple, write
     scores both back to Langfuse (.score on the trace) and into the local DB
  4. Aggregate cost/score summaries split by pass/fail, write to test_runs.summary_scores

Why we manually wrap (vs. dataset.run_experiment):
  - Self-hosted langfuse is v3.172 server; the v4 SDK's run_experiment expects a
    newer server, so we use SDK v3 low-level APIs (item.observe + score)
  - Lets us cap concurrency with our own semaphore and react to user "stop" requests

Run state lives in the in-process _RUN_REGISTRY plus the test_runs table.
A server restart during a run will leave the row in 'running' — the API layer's
on_startup hook should sweep those to 'interrupted'.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from langfuse import Langfuse
from sqlalchemy import select

from agent_eval.config import settings
from agent_eval.data._utils import truncate
from agent_eval.db import async_session_factory
from agent_eval.db_models.repository import Repository
from agent_eval.db_models.tables import BenchmarkCaseRow
from agent_eval.evaluation.agent_adapter import (
    AgentResponse,
    OpenAICompatibleAdapter,
    SSEStreamAdapter,
)
from agent_eval.evaluation.scorers.llm_judge import (
    JudgeDimension,
    LLMJudgeScorer,
)

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


async def _evaluator_llm_judge(
    *, input: str, output: str, expected_output: str | None,
    params: dict, llm_client: Any, **_,
) -> EvaluatorResult:
    """Run LLMJudgeScorer; emit one score per dimension on a 0..1 scale."""
    if llm_client is None:
        return EvaluatorResult([("llm_judge_error", 0.0, "no llm client configured")])
    dim_cfgs = params.get("dimensions") or [
        {"name": "accuracy", "weight": 0.4, "description": "答案是否准确、事实正确"},
        {"name": "completeness", "weight": 0.3, "description": "答案是否完整"},
        {"name": "relevance", "weight": 0.3, "description": "答案是否切题"},
    ]
    judge = LLMJudgeScorer(
        llm=llm_client,
        dimensions=[
            JudgeDimension(name=d["name"], weight=d.get("weight", 1.0),
                          description=d.get("description", ""))
            for d in dim_cfgs
        ],
        system_prompt=params.get("system_prompt"),
        user_template=params.get("user_template"),
    )
    question = input
    if expected_output:
        question = f"{input}\n\n[期望答案] {expected_output}"
    result = await judge.score(question, output)
    # Emit each dimension score normalised to 0..1 (LLMJudge is 1-10 scale)
    out: list[tuple[str, float, str]] = []
    for dim in result.dimensions:
        out.append((
            f"llm_judge.{dim.dimension}",
            round(dim.score / 10.0, 3),
            dim.reason or "",
        ))
    out.append((
        "llm_judge.aggregate",
        round(result.aggregate_score / 10.0, 3),
        truncate(result.raw_response, 200),
    ))
    return EvaluatorResult(out)


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
    "llm_judge": {
        "fn": _evaluator_llm_judge, "is_async": True,
        "description": "用 LLM 当裁判按维度打分。可自定义 prompt 与 dimensions。",
        "params_schema": {
            "dimensions": {"type": "array", "default": []},
            "system_prompt": {"type": "string"},
            "user_template": {"type": "string"},
        },
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
    cache_create = _avg("cache_creation_tokens")
    cache_read = _avg("cache_read_tokens")
    cache_hit_rate = None
    if prompt is not None and cache_read is not None:
        denom = (prompt or 0) - (cache_create or 0)
        if denom > 0:
            cache_hit_rate = round((cache_read or 0) / denom, 3)

    return {
        "count": n,
        "avg_prompt_tokens": prompt,
        "avg_completion_tokens": _avg("completion_tokens"),
        "avg_total_tokens": _avg("total_tokens"),
        "avg_tool_calls": _avg("tool_call_count"),
        "avg_messages": _avg("message_count"),
        "avg_latency_ms": _avg("latency_ms"),
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


def _make_adapter(agent_cfg: dict) -> Any:
    t = agent_cfg.get("type", "openai")
    if t == "openai":
        return OpenAICompatibleAdapter(
            base_url=agent_cfg["url"],
            api_key=agent_cfg.get("api_key", ""),
            model=agent_cfg.get("model", "default"),
            timeout=float(agent_cfg.get("timeout", 120.0)),
            extra_headers=agent_cfg.get("headers") or None,
        )
    if t == "sse":
        return SSEStreamAdapter(
            url=agent_cfg["url"],
            headers=agent_cfg.get("headers"),
            payload_template=agent_cfg.get("payload_template"),
            timeout=float(agent_cfg.get("timeout", 120.0)),
        )
    raise ValueError(f"unknown agent type: {t!r}")


def _make_judge_llm() -> Any | None:
    """Build a ChatOpenAI client for llm_judge using settings.llm."""
    if not settings.llm.api_key:
        return None
    try:
        from langchain_openai import ChatOpenAI
        kwargs = {
            "model": settings.llm.judge_model or settings.llm.model,
            "api_key": settings.llm.api_key,
            "temperature": 0.0,
        }
        if settings.llm.base_url:
            kwargs["base_url"] = settings.llm.base_url
        return ChatOpenAI(**kwargs)
    except Exception as e:
        logger.warning("could not build judge LLM: %s", e)
        return None


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


async def _run_one_case(
    *,
    langfuse: Langfuse,
    dataset_run_name: str,
    item: Any,  # langfuse DatasetItemClient
    case: BenchmarkCaseRow,
    agent_cfg: dict,
    evaluator_cfgs: list[dict],
    judge_llm: Any,
) -> dict[str, Any]:
    """Execute one case end-to-end. Returns a dict with all info to write to DB."""
    adapter = _make_adapter(agent_cfg)
    question = case.question
    messages = [{"role": "user", "content": question}]
    expected = case.reference_answer or ""

    output_text = ""
    error_msg: str | None = None
    error_type: str | None = None
    actual_tool_calls: list[dict] = []
    latency_ms: int | None = None
    usage = {
        "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
        "cache_creation_tokens": None, "cache_read_tokens": None,
    }
    trace_id: str | None = None
    scores: dict[str, float] = {}

    try:
        # v3 API: item.run() is a context manager that yields a LangfuseSpan,
        # bound to a trace linked to this dataset-item/run pair.
        with item.run(
            run_name=dataset_run_name,
            run_metadata={"benchmark_case_id": str(case.id), "model": agent_cfg.get("model")},
        ) as root_span:
            trace_id = getattr(root_span, "trace_id", None)
            with root_span.start_as_current_generation(
                name=agent_cfg.get("model", "agent-call"),
                input=messages,
                model=agent_cfg.get("model"),
            ) as generation:
                try:
                    resp = await adapter.invoke(messages)
                    output_text = resp.content
                    latency_ms = int(resp.latency_ms)
                    usage = _extract_usage(resp)
                    actual_tool_calls = _extract_tool_calls_from_response(resp)
                    generation.update(
                        output=output_text,
                        usage_details={
                            k: v for k, v in usage.items()
                            if v is not None and k in ("prompt_tokens", "completion_tokens", "total_tokens")
                        } or None,
                    )
                except Exception as e:
                    generation.update(level="ERROR", status_message=str(e))
                    error_msg = str(e)
                    error_type = type(e).__name__
                    logger.warning("agent invoke failed for case %s: %s", case.id, e)

            # Run evaluators; attach scores to this trace via root_span.score_trace
            if not error_msg:
                expected_tool_calls = []
                if case.extra_fields and isinstance(case.extra_fields, dict):
                    for t in case.extra_fields.get("expected_tool_calls", []) or []:
                        expected_tool_calls.append({"tool_name": t.get("tool_name") or t.get("name")})
                for ev_cfg in evaluator_cfgs:
                    name = ev_cfg["name"]
                    spec = BUILTIN_EVALUATORS.get(name)
                    if spec is None:
                        logger.warning("unknown evaluator: %s", name)
                        continue
                    fn = spec["fn"]
                    kwargs = {
                        "input": question,
                        "output": output_text,
                        "expected_output": expected,
                        "expected_tool_calls": expected_tool_calls,
                        "actual_tool_calls": actual_tool_calls,
                        "params": ev_cfg.get("params") or {},
                        "llm_client": judge_llm,
                    }
                    try:
                        result = await fn(**kwargs) if spec["is_async"] else fn(**kwargs)
                        for score_name, value, comment in result.scores:
                            scores[score_name] = value
                            try:
                                root_span.score_trace(
                                    name=score_name,
                                    value=float(value),
                                    comment=truncate(comment, 200) if comment else None,
                                )
                            except Exception as se:
                                logger.warning("failed to push score %s to langfuse: %s", score_name, se)
                    except Exception as e:
                        logger.warning("evaluator %s crashed on case %s: %s", name, case.id, e)
    finally:
        try:
            await adapter.close()
        except Exception:
            pass

    # status: pass if all evaluator scores >= 0.5 and no error
    status = "pass"
    if error_msg:
        status = "error"
    elif scores and any(v < 0.5 for v in scores.values()):
        status = "fail"

    return {
        "case_id": str(case.id),
        "trace_id": trace_id,
        "status": status,
        "actual_output": output_text,
        "actual_tool_calls": actual_tool_calls,
        "latency_ms": latency_ms,
        "error_message": error_msg,
        "error_type": error_type,
        "tool_call_count": len(actual_tool_calls),
        "message_count": len(messages),
        "scores": scores,
        **usage,
    }


async def _execute_run(
    run_id: str,
    scope_id: str,
    cases: list[BenchmarkCaseRow],
    agent_cfg: dict,
    evaluator_cfgs: list[dict],
    concurrency: int,
    langfuse_run_name: str,
    cancel_event: asyncio.Event,
    handle: _RunHandle,
) -> None:
    """The asyncio.Task body. Owns its own DB sessions per write, langfuse client."""
    handle.progress["total"] = len(cases)
    langfuse = _langfuse_client()
    judge_llm = _make_judge_llm()

    # Step 1: ensure a langfuse dataset exists named by scope_id (version or project)
    dataset_name = f"benchmark-{scope_id}"
    try:
        langfuse.create_dataset(name=dataset_name, description=f"scope={scope_id}")
    except Exception:
        pass  # likely exists

    # Step 2: ensure each case has a dataset_item (idempotent on input)
    case_to_item: dict[str, Any] = {}
    create_errors = 0
    for case in cases:
        triple = _bench_case_to_dataset_input(case)
        try:
            langfuse.create_dataset_item(
                dataset_name=dataset_name,
                input=triple["input"],
                expected_output=triple["expected_output"],
                metadata=triple["metadata"],
            )
        except Exception as e:
            create_errors += 1
            if create_errors <= 3:
                logger.warning("create_dataset_item failed for case %s: %s", case.id, e)
    if create_errors:
        logger.warning("create_dataset_item: %d failures total (suppressed after first 3)", create_errors)
    # Force flush so newly-created items are durable on the server
    try:
        langfuse.flush()
    except Exception:
        pass
    # Pull the dataset back so we have items with an `.observe` context manager
    try:
        ds = langfuse.get_dataset(dataset_name)
        logger.info("dataset %s has %d items", dataset_name, len(ds.items))
        for item in ds.items:
            md = getattr(item, "metadata", None) or {}
            case_id = md.get("benchmark_case_id") if isinstance(md, dict) else None
            if case_id:
                case_to_item[case_id] = item
        logger.info("matched %d local cases to dataset items", len(case_to_item))
    except Exception as e:
        logger.exception("get_dataset failed: %s", e)
        async with async_session_factory() as session:
            repo = Repository(session)
            await repo.finish_test_run(uuid.UUID(run_id), {"error": str(e)}, status="failed")
            await session.commit()
        return

    if not case_to_item:
        # Could not bind any cases to a dataset item — likely create_dataset_item failed
        logger.warning("no items matched; failing run %s", run_id)
        async with async_session_factory() as session:
            repo = Repository(session)
            await repo.finish_test_run(
                uuid.UUID(run_id),
                {"error": "could not create or match any langfuse dataset items",
                 "dataset": dataset_name, "case_count": len(cases)},
                status="failed",
            )
            await session.commit()
        _RUN_REGISTRY.pop(run_id, None)
        return

    # Step 3: run cases under semaphore
    sem = asyncio.Semaphore(max(1, concurrency))
    per_case_results: list[dict[str, Any]] = []

    async def _do_one(case: BenchmarkCaseRow):
        if cancel_event.is_set():
            return
        item = case_to_item.get(str(case.id))
        if item is None:
            handle.progress["failed"] += 1
            return
        async with sem:
            if cancel_event.is_set():
                return
            try:
                res = await _run_one_case(
                    langfuse=langfuse,
                    dataset_run_name=langfuse_run_name,
                    item=item, case=case,
                    agent_cfg=agent_cfg,
                    evaluator_cfgs=evaluator_cfgs,
                    judge_llm=judge_llm,
                )
            except Exception as e:
                logger.exception("case %s crashed during run: %s", case.id, e)
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
                    created = await repo.create_test_result(
                        uuid.UUID(run_id),
                        benchmark_case_id=case.id,
                        actual_output=res["actual_output"],
                        actual_tool_calls=res["actual_tool_calls"] or None,
                        latency_ms=res["latency_ms"],
                        total_tokens=res["total_tokens"],
                        prompt_tokens=res["prompt_tokens"],
                        completion_tokens=res["completion_tokens"],
                        tool_call_count=res["tool_call_count"],
                        error_message=res["error_message"],
                        error_type=res["error_type"],
                        status=res["status"],
                        full_trace={"langfuse_trace_id": res["trace_id"]} if res["trace_id"] else None,
                        langfuse_trace_id=res["trace_id"],
                    )
                    for sname, sval in res["scores"].items():
                        await repo.create_eval_score(
                            created.id, dimension=sname, score=sval,
                            weight=1.0, weighted_score=sval, scoring_method="langfuse",
                            details={},
                        )
                    await session.commit()
            except Exception as e:
                logger.exception("failed to persist results for case %s: %s", case.id, e)

    try:
        await asyncio.gather(*[_do_one(c) for c in cases])
    finally:
        # Aggregate
        succ = [r for r in per_case_results if r["status"] == "pass"]
        fail = [r for r in per_case_results if r["status"] != "pass"]
        # Per-dimension averages over passing rows (so a few failures don't tank the median)
        all_scores: dict[str, list[float]] = {}
        for r in per_case_results:
            for k, v in r["scores"].items():
                all_scores.setdefault(k, []).append(v)
        dim_avg = {k: round(sum(vs) / len(vs), 3) for k, vs in all_scores.items() if vs}

        summary = {
            "counts": {
                "total": len(per_case_results),
                "passed": len(succ),
                "failed": len(fail),
            },
            "dimension_averages": dim_avg,
            "cost_success": _aggregate_cost(succ),
            "cost_failure": _aggregate_cost(fail),
            "langfuse_dataset": dataset_name,
            "langfuse_run_name": langfuse_run_name,
        }
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

        try:
            langfuse.flush()
        except Exception:
            pass
        _RUN_REGISTRY.pop(run_id, None)


# ───────────────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────────────


async def start_run(
    *,
    benchmark_version_id: str | None = None,
    project_id: str | None = None,
    cases: list[BenchmarkCaseRow],
    agent_cfg: dict,
    evaluator_cfgs: list[dict],
    concurrency: int = 3,
    run_name: str | None = None,
) -> str:
    """Create a test_runs row, register an asyncio task, return run_id."""
    if not cases:
        raise ValueError("no cases selected")
    if not evaluator_cfgs:
        raise ValueError("at least one evaluator required")
    if not settings.langfuse.configured:
        raise RuntimeError("Langfuse not configured")

    run_name = run_name or f"eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.create_test_run(
            benchmark_version_id=uuid.UUID(benchmark_version_id) if benchmark_version_id else None,
            agent_config=agent_cfg,
            langfuse_run_name=run_name,
            evaluator_configs=evaluator_cfgs,
            status="running",
        )
        await session.commit()
        run_id = str(row.id)

    # Use project_id as the langfuse dataset name when no version is set.
    scope_id = benchmark_version_id or project_id or "default"
    cancel_event = asyncio.Event()
    handle = _RunHandle(run_id=run_id, task=None, cancel_event=cancel_event)  # type: ignore[arg-type]
    handle.task = asyncio.create_task(_execute_run(
        run_id=run_id,
        scope_id=scope_id,
        cases=cases,
        agent_cfg=agent_cfg,
        evaluator_cfgs=evaluator_cfgs,
        concurrency=concurrency,
        langfuse_run_name=run_name,
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
