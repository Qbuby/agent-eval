"""持久化 agent 回复的生成引擎。

给数据集页面的「agent生成答案」提供服务端异步批量生成：把选中样例的 agent
回复跑出来，落成 ``agent_reply_versions`` 的一条版本行，并把该样例的当前版本
指针指向它。

与评估 run 的关系：这里**只调 agent、不打分**，是 ``langfuse_runner`` 的对偶
——runner 里 rescore 是「有回复不调 agent 只打分」，这里是「调 agent 不打分」。
adapter 工厂 / 重试器 / 多轮回放全部复用 runner 与 multiturn 的现成实现，避免
两套调用语义分叉。

任务状态**落库**（不像 rescore 只在内存 dict）：刷新页面能恢复进度，进程重启
由 ``sweep_orphaned_agent_reply_jobs`` 收尾。取消能力照 ``_RunHandle`` 的
cancel_event 范式，逐样例入口检查。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from agent_eval.db import async_session_factory
from agent_eval.db_models.repository import Repository
from agent_eval.db_models.tenant_context import (
    TenantContext,
    reset_tenant_context,
    set_tenant_context,
)

logger = logging.getLogger(__name__)

# 三类数据集的类型标识（也是 agent_reply_* 表 dataset_type 列的取值域）。
DATASET_TYPES = ("candidate", "benchmark", "conversation")

# 生成侧默认并发。前端可覆盖，上限与评估 run 对齐（20）。
DEFAULT_CONCURRENCY = 3
MAX_CONCURRENCY = 20


def config_fingerprint(agent_cfg: dict[str, Any]) -> str:
    """agent 配置的归一化指纹（sha256 前 64 位十六进制）。

    用于「同一样例 + 同一 agent 配置已有在途任务」的去重判定；不同配置可并行，
    所以指纹必须覆盖所有影响回复的字段。api_key 参与计算（换 key 视为换配置），
    但只进哈希不落明文。归一化 = 按 key 排序 + 紧凑分隔符，保证字段顺序无关。
    """
    payload = {
        "type": agent_cfg.get("type") or "sse",
        "url": agent_cfg.get("url") or "",
        "model": agent_cfg.get("model") or "",
        "api_key": agent_cfg.get("api_key") or "",
        "headers": agent_cfg.get("headers") or {},
        "payload_template": agent_cfg.get("payload_template") or {},
        "timeout": float(agent_cfg.get("timeout") or 120.0),
        "language": agent_cfg.get("language") or "",
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def redact_agent_config(agent_cfg: dict[str, Any]) -> dict[str, Any]:
    """落库/回传前抹掉凭证。版本行要留配置快照供复现，但不该存明文 key。"""
    safe = dict(agent_cfg or {})
    if safe.get("api_key"):
        safe["api_key"] = "***"
    headers = safe.get("headers")
    if isinstance(headers, dict):
        safe["headers"] = {
            k: ("***" if k.lower() in ("authorization", "x-api-key", "api-key") else v)
            for k, v in headers.items()
        }
    return safe


@dataclass
class _JobHandle:
    job_id: str
    task: asyncio.Task | None
    cancel_event: asyncio.Event
    progress: dict[str, int] = field(
        default_factory=lambda: {"total": 0, "succeeded": 0, "failed": 0, "running": 0}
    )


_JOB_REGISTRY: dict[str, _JobHandle] = {}


def get_job_progress(job_id: str) -> dict[str, int]:
    """内存进度快照。DB 里也有一份（每条完成即写），这里主要给「刚点下去还没
    落库」的瞬间用；无在途任务返回空 dict，调用方回落到 DB 计数。"""
    h = _JOB_REGISTRY.get(job_id)
    return dict(h.progress) if h else {}


def request_cancel(job_id: str) -> bool:
    """请求取消剩余样例。已在跑的那条跑完为止（不硬杀），后续 pending 直接标
    cancelled。返回 False 表示本进程没有这个在途任务（可能已结束或换了进程）。"""
    h = _JOB_REGISTRY.get(job_id)
    if h is None:
        return False
    h.cancel_event.set()
    return True


def is_job_active(job_id: str) -> bool:
    return job_id in _JOB_REGISTRY


# ───────────────────────────────────────────────────────────────────────────
# 单样例生成
# ───────────────────────────────────────────────────────────────────────────


async def generate_one_reply(
    *,
    case: dict[str, Any],
    agent_cfg: dict[str, Any],
    cancel_event: asyncio.Event | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """跑一条样例的 agent 回复。不打分、不写库，纯粹产出内容。

    ``case`` 形状与 runner 的归一化 case 一致：``{id, question, multi_turn,
    input_messages}``。多轮时走 ``multiturn.replay_conversation``，产出的 turns
    与评估回放**同构**，这样评估侧复用 ``score_conversation`` /
    ``build_transcript`` 时零改动。

    返回 ``{content, turns, raw_trace, latency_ms, total_tokens, error}``；
    ``error`` 非空即失败（异常已在内部吸收，不向上抛）。
    """
    from agent_eval.evaluation import multiturn
    from agent_eval.evaluation.langfuse_runner import (
        _classify_agent_error,
        _invoke_with_retry,
        _make_adapter,
        _retry_policy_from_cfg,
    )

    is_multi = bool(case.get("multi_turn"))
    thread_id = f"reply-{case.get('id', 'case')}-{uuid.uuid4().hex[:8]}"
    adapter = _make_adapter(agent_cfg, thread_id=thread_id, client=http_client)
    policy = _retry_policy_from_cfg(agent_cfg)

    async def _invoke(adp: Any, msgs: list[dict[str, Any]]):
        return await _invoke_with_retry(
            adp, msgs, policy=policy, cancel_event=cancel_event,
        )

    out: dict[str, Any] = {
        "content": None,
        "turns": None,
        "raw_trace": None,
        "latency_ms": None,
        "total_tokens": None,
        "error": None,
        "error_type": None,
    }
    try:
        if is_multi:
            replay = await multiturn.replay_conversation(
                adapter=adapter,
                agent_type=agent_cfg.get("type", "sse"),
                input_messages=case.get("input_messages") or [],
                invoke=_invoke,
            )
            turns = replay.get("turns") or []
            out["turns"] = turns
            out["content"] = multiturn.build_transcript(turns)
            out["latency_ms"] = replay.get("latency_ms")
            usage = replay.get("usage") or {}
            out["total_tokens"] = usage.get("total_tokens")
            out["raw_trace"] = {
                "steps": replay.get("steps") or [],
                "tool_calls": replay.get("tool_calls") or [],
                "usage": usage,
                "attempts": replay.get("attempts"),
                "thread_id": thread_id,
            }
            # 逐轮容错：某轮失败时前面已完成的轮仍保留，但整条记为失败——
            # 半截对话不该被当成可用于评估的回复。
            if replay.get("error"):
                out["error"] = replay["error"]
                exc = replay.get("error_exc")
                out["error_type"] = (
                    _classify_agent_error(exc) if exc is not None else "error"
                )
            elif not turns:
                out["error"] = "对话没有可回放的 user 轮次"
                out["error_type"] = "empty_conversation"
        else:
            question = (case.get("question") or "").strip()
            if not question:
                out["error"] = "样例没有问题内容，无法生成回复"
                out["error_type"] = "empty_question"
                return out
            resp, attempts = await _invoke(
                adapter, [{"role": "user", "content": question}]
            )
            out["content"] = resp.content or ""
            out["latency_ms"] = int(getattr(resp, "latency_ms", 0) or 0)
            raw = getattr(resp, "raw_response", None)
            trace: dict[str, Any] = {"attempts": attempts, "thread_id": thread_id}
            if isinstance(raw, dict):
                trace["steps"] = raw.get("steps") or []
                trace["tool_calls"] = raw.get("tool_calls") or []
                usage = raw.get("usage")
                if isinstance(usage, dict):
                    trace["usage"] = usage
                    tot = usage.get("total_tokens")
                    if isinstance(tot, int):
                        out["total_tokens"] = tot
            if out["total_tokens"] is None:
                tk = getattr(resp, "token_count", None)
                if isinstance(tk, int):
                    out["total_tokens"] = tk
            out["raw_trace"] = trace
    except Exception as e:
        attempts = getattr(e, "_eval_attempts_made", 1)
        msg = str(e)
        if attempts > 1:
            msg = f"{msg} (after {attempts} attempts)"
        out["error"] = msg
        out["error_type"] = _classify_agent_error(e)
        logger.warning(
            "reply generation failed for case %s [%s]: %s",
            case.get("id"), out["error_type"], e,
        )
    finally:
        try:
            await adapter.close()
        except Exception:
            pass
    return out


# ───────────────────────────────────────────────────────────────────────────
# 批量任务
# ───────────────────────────────────────────────────────────────────────────


async def start_generation_job(
    *,
    dataset_type: str,
    cases: list[dict[str, Any]],
    agent_cfg: dict[str, Any],
    dataset_name: str | None = None,
    project_id: uuid.UUID | None = None,
    version_label: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    created_by: uuid.UUID | None = None,
    tenant_ctx: TenantContext | None = None,
) -> str:
    """建任务 + 逐样例 item 落库，然后后台跑。返回 job_id。

    调用方（HTTP 层）负责：样例解析、在途去重检查、权限。这里只管执行。
    ``tenant_ctx`` 由调用方从请求上下文取，后台任务里重新 set 一次——
    ``asyncio.create_task`` 不继承 contextvar 的后续修改，不显式带会导致写入
    落到内部 sentinel 租户。
    """
    if dataset_type not in DATASET_TYPES:
        raise ValueError(f"unknown dataset_type: {dataset_type!r}")
    if not cases:
        raise ValueError("no cases to generate")

    fingerprint = config_fingerprint(agent_cfg)
    async with async_session_factory() as session:
        repo = Repository(session)
        job = await repo.create_agent_reply_job(
            dataset_type=dataset_type,
            agent_config=redact_agent_config(agent_cfg),
            config_fingerprint=fingerprint,
            dataset_name=dataset_name,
            project_id=project_id,
            version_label=version_label,
            total_count=len(cases),
            created_by=created_by,
        )
        job_id = str(job.id)
        item_ids: list[tuple[str, str]] = []
        for c in cases:
            item = await repo.create_agent_reply_job_item(
                job_id=job.id,
                case_ref=str(c.get("id")),
                question=(c.get("question") or None),
            )
            item_ids.append((str(c.get("id")), str(item.id)))
        await session.commit()

    cancel_event = asyncio.Event()
    handle = _JobHandle(job_id=job_id, task=None, cancel_event=cancel_event)
    handle.progress["total"] = len(cases)
    handle.progress["running"] = len(cases)
    handle.task = asyncio.create_task(
        _execute_job(
            job_id=job_id,
            dataset_type=dataset_type,
            cases=cases,
            item_ids=dict(item_ids),
            agent_cfg=agent_cfg,
            dataset_name=dataset_name,
            project_id=project_id,
            version_label=version_label,
            fingerprint=fingerprint,
            concurrency=concurrency,
            created_by=created_by,
            cancel_event=cancel_event,
            handle=handle,
            tenant_ctx=tenant_ctx,
        )
    )
    _JOB_REGISTRY[job_id] = handle
    return job_id


async def _persist_one(
    *,
    job_id: str,
    item_id: str,
    dataset_type: str,
    case: dict[str, Any],
    result: dict[str, Any],
    agent_cfg: dict[str, Any],
    dataset_name: str | None,
    project_id: uuid.UUID | None,
    version_label: str | None,
    fingerprint: str,
    created_by: uuid.UUID | None,
) -> bool:
    """把一条生成结果落成版本行 + 更新 item / job 计数。返回是否成功。

    失败也建版本行（status='failed'），这样 UI 能看到「这次生成为什么失败」，
    但**不**把当前版本指针指向它——当前版本必须是可用于评估的回复。
    """
    ok = result.get("error") is None
    async with async_session_factory() as session:
        repo = Repository(session)
        version = await repo.create_agent_reply_version(
            dataset_type=dataset_type,
            case_ref=str(case.get("id")),
            agent_config=redact_agent_config(agent_cfg),
            dataset_name=dataset_name,
            project_id=project_id,
            version_label=version_label,
            content=result.get("content"),
            turns=result.get("turns"),
            raw_trace=result.get("raw_trace"),
            config_fingerprint=fingerprint,
            status="succeeded" if ok else "failed",
            error_message=result.get("error"),
            latency_ms=result.get("latency_ms"),
            total_tokens=result.get("total_tokens"),
            job_id=uuid.UUID(job_id),
            created_by=created_by,
        )
        if ok:
            await repo.set_current_agent_reply_version(
                dataset_type=dataset_type,
                case_ref=str(case.get("id")),
                version_id=version.id,
                dataset_name=dataset_name,
                project_id=project_id,
            )
        await repo.update_agent_reply_job_item(
            uuid.UUID(item_id),
            status="succeeded" if ok else "failed",
            version_id=version.id,
            error_message=result.get("error"),
            finished_at=datetime.now(timezone.utc),
        )
        job = await repo.get_agent_reply_job(uuid.UUID(job_id))
        if job is not None:
            if ok:
                job.succeeded_count = int(job.succeeded_count or 0) + 1
            else:
                job.failed_count = int(job.failed_count or 0) + 1
            job.running_count = max(0, int(job.running_count or 0) - 1)
            job.updated_at = datetime.now(timezone.utc)
        await session.commit()
    return ok


async def _execute_job(
    *,
    job_id: str,
    dataset_type: str,
    cases: list[dict[str, Any]],
    item_ids: dict[str, str],
    agent_cfg: dict[str, Any],
    dataset_name: str | None,
    project_id: uuid.UUID | None,
    version_label: str | None,
    fingerprint: str,
    concurrency: int,
    created_by: uuid.UUID | None,
    cancel_event: asyncio.Event,
    handle: _JobHandle,
    tenant_ctx: TenantContext | None,
) -> None:
    """后台任务体。并发跑 agent，逐条落库；取消时把剩余标 cancelled。"""
    ctx_token = None
    if tenant_ctx is not None:
        ctx_token = set_tenant_context(tenant_ctx)
    sem = asyncio.Semaphore(max(1, min(int(concurrency or 1), MAX_CONCURRENCY)))
    limits = httpx.Limits(
        max_connections=max(4, concurrency * 2),
        max_keepalive_connections=max(2, concurrency),
    )
    http_client = httpx.AsyncClient(
        limits=limits, timeout=float(agent_cfg.get("timeout", 120.0))
    )
    cancelled_refs: list[str] = []

    async def _do_one(case: dict[str, Any]) -> None:
        case_ref = str(case.get("id"))
        item_id = item_ids.get(case_ref)
        if item_id is None:
            return
        # 取消检查放在闸门两侧：已排队但还没轮到的直接标 cancelled，
        # 与 _execute_run 的 _do_one 同范式。
        if cancel_event.is_set():
            cancelled_refs.append(case_ref)
            return
        async with sem:
            if cancel_event.is_set():
                cancelled_refs.append(case_ref)
                return
            try:
                async with async_session_factory() as session:
                    repo = Repository(session)
                    await repo.update_agent_reply_job_item(
                        uuid.UUID(item_id),
                        status="running",
                        started_at=datetime.now(timezone.utc),
                        attempts=1,
                    )
                    await session.commit()
            except Exception as e:
                logger.warning("mark item running failed (%s): %s", item_id, e)

            result = await generate_one_reply(
                case=case,
                agent_cfg=agent_cfg,
                cancel_event=cancel_event,
                http_client=http_client,
            )
            try:
                ok = await _persist_one(
                    job_id=job_id,
                    item_id=item_id,
                    dataset_type=dataset_type,
                    case=case,
                    result=result,
                    agent_cfg=agent_cfg,
                    dataset_name=dataset_name,
                    project_id=project_id,
                    version_label=version_label,
                    fingerprint=fingerprint,
                    created_by=created_by,
                )
            except Exception as e:
                logger.exception(
                    "failed to persist generated reply for case %s: %s", case_ref, e
                )
                ok = False
            handle.progress["succeeded" if ok else "failed"] += 1
            handle.progress["running"] = max(0, handle.progress["running"] - 1)

    try:
        await asyncio.gather(*[_do_one(c) for c in cases])
    finally:
        try:
            await http_client.aclose()
        except Exception:
            pass
        try:
            async with async_session_factory() as session:
                repo = Repository(session)
                now = datetime.now(timezone.utc)
                for ref in cancelled_refs:
                    item_id = item_ids.get(ref)
                    if item_id:
                        await repo.update_agent_reply_job_item(
                            uuid.UUID(item_id), status="cancelled", finished_at=now,
                        )
                job = await repo.get_agent_reply_job(uuid.UUID(job_id))
                if job is not None:
                    job.running_count = 0
                    if cancel_event.is_set():
                        job.status = "cancelled"
                    elif int(job.succeeded_count or 0) == 0:
                        job.status = "failed"
                    else:
                        job.status = "completed"
                    job.finished_at = now
                    job.updated_at = now
                await session.commit()
        except Exception as e:
            logger.exception("failed to finalize reply job %s: %s", job_id, e)
        _JOB_REGISTRY.pop(job_id, None)
        if ctx_token is not None:
            reset_tenant_context(ctx_token)


class PersistedReplyAdapter:
    """把预生成回复冒充成 agent adapter，让评估侧零改动复用持久化回复。

    评估的三条路径（单轮 / 多轮 / 双模对比）都只通过 ``invoke(messages) ->
    AgentResponse`` 与 agent 交互，多轮更是由 ``multiturn.replay_conversation``
    逐轮调用同一 adapter。故只要提供一个同接口的假 adapter，就能让 runner 完全
    不建 SSE、不调 agent，而所有打分 / 聚合 / 落库逻辑保持原样。

    - 单轮：``content`` 为整条回复，任何 invoke 都返回它。
    - 多轮：``turns`` 按 ``turn_index`` 顺序逐轮返回对应 assistant 文本；
      replay 每轮调一次，故用内部游标推进，与回放顺序天然对齐。
    - ``raw_trace`` 里的 steps / tool_calls / usage 原样回填进 raw_response，
      使详情页的工具调用、CoT、token 展示与实时评估一致。
    """

    def __init__(
        self,
        *,
        content: str | None,
        turns: list[dict[str, Any]] | None = None,
        raw_trace: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        self._content = content or ""
        self._turns = list(turns or [])
        self._raw_trace = raw_trace or {}
        self._latency_ms = float(latency_ms or 0)
        self._total_tokens = total_tokens
        self._cursor = 0

    def _turn_payload(self, idx: int) -> dict[str, Any]:
        if 0 <= idx < len(self._turns):
            t = self._turns[idx]
            if isinstance(t, dict):
                return t
        return {}

    async def invoke(self, messages: list[dict[str, Any]]) -> Any:
        from agent_eval.evaluation.agent_adapter import AgentResponse

        if self._turns:
            turn = self._turn_payload(self._cursor)
            self._cursor += 1
            raw = {
                "steps": turn.get("steps") or [],
                "tool_calls": turn.get("tool_calls") or [],
                "usage": turn.get("usage") or {},
                "persisted_reply": True,
            }
            usage = turn.get("usage") or {}
            return AgentResponse(
                content=turn.get("assistant") or "",
                latency_ms=float(turn.get("latency_ms") or 0),
                token_count=usage.get("total_tokens"),
                raw_response=raw,
            )

        raw = {
            "steps": self._raw_trace.get("steps") or [],
            "tool_calls": self._raw_trace.get("tool_calls") or [],
            "usage": self._raw_trace.get("usage") or {},
            "persisted_reply": True,
        }
        return AgentResponse(
            content=self._content,
            latency_ms=self._latency_ms,
            token_count=self._total_tokens,
            raw_response=raw,
        )

    async def close(self) -> None:
        return None


def adapter_from_version(row: Any) -> PersistedReplyAdapter:
    """用一条 ``AgentReplyVersionRow`` 造出可喂给 runner 的假 adapter。"""
    return PersistedReplyAdapter(
        content=row.content,
        turns=row.turns,
        raw_trace=row.raw_trace,
        latency_ms=row.latency_ms,
        total_tokens=row.total_tokens,
    )


async def resolve_reply_versions(
    *,
    dataset_type: str,
    case_refs: list[str],
    version_ids: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """解析评估要消费的回复版本。

    ``version_ids`` 显式指定 case_ref -> version_id 时用它；未指定的样例回落到
    ``agent_reply_case_states.current_version_id``。返回
    ``(by_case_ref, missing_refs)``——``missing_refs`` 是没有可用回复（无版本、
    版本不属于该样例、或版本不是 succeeded）的样例，调用方据此拒绝启动评估，
    而不是静默跳过导致「评估结果里少了样例」这种难查的偏差。
    """
    explicit = {str(k): str(v) for k, v in (version_ids or {}).items()}
    resolved: dict[str, Any] = {}
    missing: list[str] = []

    async with async_session_factory() as session:
        repo = Repository(session)
        states = await repo.list_agent_reply_case_states(dataset_type, case_refs)
        state_by_ref = {s.case_ref: s for s in states}

        for ref in case_refs:
            row = None
            vid_raw = explicit.get(ref)
            if vid_raw:
                try:
                    row = await repo.get_agent_reply_version(uuid.UUID(vid_raw))
                except (TypeError, ValueError):
                    row = None
                if row is not None and (
                    row.dataset_type != dataset_type or row.case_ref != ref
                ):
                    row = None
            else:
                st = state_by_ref.get(ref)
                if st is not None and st.current_version_id is not None:
                    row = await repo.get_agent_reply_version(st.current_version_id)

            if row is None or row.status != "succeeded":
                missing.append(ref)
                continue
            resolved[ref] = row

    return resolved, missing


async def sweep_orphaned_jobs() -> int:
    """启动时把上个进程残留的 running 任务标 interrupted。挂在 app lifespan。"""
    async with async_session_factory() as session:
        repo = Repository(session)
        n = await repo.sweep_orphaned_agent_reply_jobs()
        await session.commit()
    return n
