"""Langfuse 指标展示（内部 admin）。

后台轮询把 Langfuse 的 trace / observation 级指标周期拉取并落库
（``langfuse_trace_metrics`` / ``langfuse_observation_metrics``，单例游标
``langfuse_metrics_cursors``）。本 router 给内部 admin 跨 environment 只读这些
指标 + 手动触发一次轮询。

全部端点 ``require_role(ROLE_ADMIN)``：内部 admin 登录态是 superadmin，db.py
读监听器对 superadmin **旁路过滤**，且这三张表由后台无租户上下文写入（自动落
INTERNAL_TENANT_ID）。所以这里直连 ``async_session_factory()`` 查询即可跨 env
全见，**无需手写** ``.where(tenant_id==...)``。

轮询服务实例由启动代码经 ``set_service()`` 注入（仿 scheduler.py 的模块级单例），
本模块**不 import service 模块**以避免循环依赖；``run_once`` 通过注入实例调用。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import load_only

from agent_eval.auth.dependencies import require_internal
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    CandidateCaseRow,
    LangfuseMetricsCursorRow,
    LangfuseObservationMetricRow,
    LangfuseTraceMetricRow,
)
from agent_eval.governance.helpers import log_audit

# 内部角色（admin + 内部普通 user）可见：require_internal = admin|user。
# 这三张表由后台无租户上下文写入（落 INTERNAL_TENANT_ID）；内部 user 登录态虽非
# superadmin，但这些表的 tenant_id 恒为 INTERNAL，且 db.py 读监听器对「无显式租户
# 上下文不匹配」不会误伤同租户行——内部 user 属内部租户，可读 INTERNAL 数据。
router = APIRouter(
    prefix="/api/langfuse-metrics",
    tags=["langfuse-metrics"],
    dependencies=[Depends(require_internal())],
)


# --------------------------------------------------------------------------- #
# 注入：轮询服务模块级单例（仿 scheduler.py）
# --------------------------------------------------------------------------- #
_service = None


def set_service(svc) -> None:
    global _service
    _service = svc


def _get_service():
    if _service is None:
        raise HTTPException(status_code=503, detail="Langfuse metrics service not started")
    return _service


def _round(value, ndigits: int = 2):
    """func.avg/sum 在 asyncpg 下返回 Decimal；统一转 float 再四舍五入。None 透传。"""
    if value is None:
        return None
    return round(float(value), ndigits)


def _float(value):
    """Decimal/float → float，None 透传（不四舍五入，用于原文金额/秒值字段）。"""
    if value is None:
        return None
    return float(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _input_preview(value, max_len: int = 300) -> str | None:
    """列表用 input 预览：文本化后截断，避免大 JSON 撑爆列表 payload。

    str 原样使用；dict/list 等用紧凑 JSON 序列化。超长截断并加省略号。
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    else:
        try:
            import json

            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    text = text.strip()
    if not text:
        return None
    if len(text) > max_len:
        return text[:max_len] + "…"
    return text


# --------------------------------------------------------------------------- #
# 语义执行链（CoT + 工具链）构建：从 observation 明细拼出有序步骤
# --------------------------------------------------------------------------- #
def _obs_content_blocks(value) -> list[dict]:
    """把 observation.output/input 归一成 content block 列表（对齐 compute._content_blocks）。

    支持 ``{"content": [...]}`` / ``{"content": "str"}`` / ``[block,...]`` /
    单个带 type 的块 / 顶层字符串。无法解析返回 ``[]``。
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [b for b in value if isinstance(b, dict)]
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, list):
            return [b for b in content if isinstance(b, dict)]
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        if "type" in value:
            return [value]
        return []
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    return []


def _text_from_blocks(blocks: list[dict], block_type: str) -> str:
    """拼接指定 type 的块文本（thinking / text 等）。空则返回 ""。"""
    parts = [
        b.get("text") or b.get("thinking") or ""
        for b in blocks
        if b.get("type") == block_type
    ]
    return "\n".join(p for p in parts if isinstance(p, str) and p.strip()).strip()


def _build_semantic_trace(obs_rows: list) -> dict | None:
    """从一条 trace 的 observation 明细拼出语义执行链（steps + tool_calls）。

    口径（对齐 compute.py，且**绝不臆造 CoT**）：
      - GENERATION：output 里显式的 ``thinking`` 块 → ``thought`` 步骤；
        ``text`` 块 → ``answer`` 步骤。二者都无则跳过（不把普通模型步骤伪造成思考）。
      - TOOL：一条工具调用步骤（tool_call），args=input、output=output，
        level==ERROR 或 output 含 error → 失败。
    steps 按 start_time 升序（obs_rows 调用方已排好）。无可识别内容返回 None。
    """
    steps: list[dict] = []
    tool_calls: list[dict] = []
    for o in obs_rows:
        otype = (o.type or "").upper()
        started = o.start_time.timestamp() if o.start_time is not None else None
        dur_ms = int(float(o.latency_s) * 1000) if o.latency_s is not None else None
        if otype == "TOOL":
            out = o.output
            # level==ERROR 时把错误折进 output，让前端 isToolCallError 命中
            if (o.level or "").upper() == "ERROR" and not (
                isinstance(out, dict) and out.get("error")
            ):
                out = {"error": o.status_message or "tool error", "raw": out}
            tc = {
                "tool_name": o.name or "",
                "args": o.input,
                "output": out,
                "started_at": started,
                "duration_ms": dur_ms,
            }
            tool_calls.append(tc)
            steps.append({"type": "tool_call", **tc})
        elif otype == "GENERATION":
            blocks = _obs_content_blocks(o.output)
            thinking = _text_from_blocks(blocks, "thinking")
            if thinking:
                steps.append({
                    "type": "thought",
                    "content": thinking,
                    "started_at": started,
                    "duration_ms": dur_ms,
                })
            answer = _text_from_blocks(blocks, "text")
            if answer:
                steps.append({
                    "type": "answer",
                    "content": answer,
                    "started_at": started,
                    "duration_ms": dur_ms,
                })
    if not steps and not tool_calls:
        return None
    return {
        "steps": steps,
        "tool_calls": tool_calls,
        "format": "langfuse_observations",
        "complete": True,
    }


# --------------------------------------------------------------------------- #
# 响应模型
# --------------------------------------------------------------------------- #
class MetricsStatsResponse(BaseModel):
    total_traces: int
    avg_latency_s: float | None = None
    avg_total_tokens: float | None = None
    total_tokens_sum: int | None = None
    total_cost: float | None = None
    avg_first_tool_call_s: float | None = None
    avg_first_thinking_token_s: float | None = None
    avg_first_answer_token_s: float | None = None
    tool_calls_sum: int | None = None
    tool_success_sum: int | None = None
    overall_tool_success_rate: float | None = None
    error_trace_count: int
    cache_hit_rate: float | None = None  # 恒 None，前端显示 N/A
    environments: list[str] = []


class MetricsTrendBucket(BaseModel):
    date: str
    trace_count: int
    avg_latency_s: float | None = None
    total_cost: float | None = None
    total_tokens: int | None = None
    tool_success_rate: float | None = None
    # 新增：错误趋势 + 首 token 时间趋势（支撑更丰富图表）
    error_count: int = 0
    avg_first_tool_call_s: float | None = None
    avg_first_thinking_token_s: float | None = None
    avg_first_answer_token_s: float | None = None


class MetricsTrendResponse(BaseModel):
    buckets: list[MetricsTrendBucket]


class TraceListItem(BaseModel):
    langfuse_trace_id: str
    name: str | None = None
    environment: str
    trace_timestamp: str | None = None
    latency_s: float | None = None
    total_tokens: int | None = None
    total_cost: float | None = None
    tool_call_count: int
    tool_success_rate: float | None = None
    has_error: bool
    input_preview: str | None = None


class TraceListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    traces: list[TraceListItem]


class ObservationDetail(BaseModel):
    id: str
    type: str
    name: str | None = None
    level: str | None = None
    status_message: str | None = None
    model: str | None = None
    start_time: str | None = None
    latency_s: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    calculated_total_cost: float | None = None
    time_to_first_token_s: float | None = None
    parent_observation_id: str | None = None
    # 原始 input/output（透传 JSONB），供详情抽屉展开单个 observation 内容。
    input: dict | list | str | None = None
    output: dict | list | str | None = None


class TraceDetailResponse(BaseModel):
    langfuse_trace_id: str
    name: str | None = None
    environment: str
    trace_timestamp: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    release: str | None = None
    tags: list | None = None
    latency_s: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    total_cost: float | None = None
    first_tool_call_s: float | None = None
    tool_call_count: int
    tool_success_count: int
    tool_error_count: int
    tool_success_rate: float | None = None
    tool_call_counts: dict | None = None
    first_thinking_token_s: float | None = None
    first_answer_token_s: float | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_hit_rate: float | None = None
    observation_count: int
    generation_count: int
    has_error: bool
    input: dict | None = None
    output: dict | None = None
    trace_metadata: dict | None = None
    scores: list | None = None
    observations: list[ObservationDetail]
    # 服务端归一化的语义执行链（思考 / 工具调用 / 答复），从 observations 拼出。
    # 只把 GENERATION 里显式的 thinking / text 块与 TOOL observation 转成步骤，
    # 不臆造 CoT；无可识别内容时为 None，前端回退到 observations 表 + 原始 JSON。
    semantic_trace: dict | None = None


class PollResponse(BaseModel):
    status: str
    last_run_traces: int | None = None
    last_run_observations: int | None = None


class PollStatusResponse(BaseModel):
    status: str | None = None
    last_polled_at: str | None = None
    last_window_start: str | None = None
    last_window_end: str | None = None
    traces_synced_total: int = 0
    observations_synced_total: int = 0
    last_run_traces: int = 0
    last_run_observations: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None


# --------------------------------------------------------------------------- #
# 内部：时间窗 / environment 过滤
# --------------------------------------------------------------------------- #
def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _trace_filters(environment: str | None, from_: str | None, to: str | None):
    """构造 LangfuseTraceMetricRow 上的 environment + trace_timestamp 窗口 where 列表。"""
    clauses = []
    if environment:
        clauses.append(LangfuseTraceMetricRow.environment == environment)
    dt_from = _parse_dt(from_)
    dt_to = _parse_dt(to)
    if dt_from is not None:
        clauses.append(LangfuseTraceMetricRow.trace_timestamp >= dt_from)
    if dt_to is not None:
        clauses.append(LangfuseTraceMetricRow.trace_timestamp <= dt_to)
    return clauses


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #
@router.get("/stats", response_model=MetricsStatsResponse)
async def get_metrics_stats(
    environment: str | None = Query(None, description="精确匹配某 env；不传则全 env"),
    from_: str | None = Query(None, alias="from", description="trace_timestamp 下界 (ISO)"),
    to: str | None = Query(None, description="trace_timestamp 上界 (ISO)"),
) -> MetricsStatsResponse:
    """指标总览聚合：延迟 / token / 成本 / 工具成功率 / 错误数。

    environment 过滤只作用于聚合数字；``environments`` 下拉始终返回全部 env。
    cache_hit_rate 恒 None（占位）。
    """
    clauses = _trace_filters(environment, from_, to)
    T = LangfuseTraceMetricRow

    agg_stmt = select(
        func.count().label("total_traces"),
        func.avg(T.latency_s).label("avg_latency_s"),
        func.avg(T.total_tokens).label("avg_total_tokens"),
        func.sum(T.total_tokens).label("total_tokens_sum"),
        func.sum(T.total_cost).label("total_cost"),
        func.avg(T.first_tool_call_s).label("avg_first_tool_call_s"),
        func.avg(T.first_thinking_token_s).label("avg_first_thinking_token_s"),
        func.avg(T.first_answer_token_s).label("avg_first_answer_token_s"),
        func.sum(T.tool_call_count).label("tool_calls_sum"),
        func.sum(T.tool_success_count).label("tool_success_sum"),
    )
    # 错误数单独 count(has_error=true)，避免 sum(cast(bool)) 的方言坑
    err_stmt = select(func.count()).where(T.has_error.is_(True))
    env_stmt = select(distinct(T.environment)).order_by(T.environment.asc())
    if clauses:
        agg_stmt = agg_stmt.where(*clauses)
        err_stmt = err_stmt.where(*clauses)

    async with async_session_factory() as session:
        row = (await session.execute(agg_stmt)).one()
        error_trace_count = (await session.execute(err_stmt)).scalar_one()
        environments = [e for (e,) in (await session.execute(env_stmt)).all()]

    (
        total_traces,
        avg_latency_s,
        avg_total_tokens,
        total_tokens_sum,
        total_cost,
        avg_first_tool_call_s,
        avg_first_thinking_token_s,
        avg_first_answer_token_s,
        tool_calls_sum,
        tool_success_sum,
    ) = row

    calls = int(tool_calls_sum or 0)
    success = int(tool_success_sum or 0)
    overall_rate = _round(success / calls, 4) if calls > 0 else None

    return MetricsStatsResponse(
        total_traces=int(total_traces or 0),
        avg_latency_s=_round(avg_latency_s),
        avg_total_tokens=_round(avg_total_tokens),
        total_tokens_sum=int(total_tokens_sum) if total_tokens_sum is not None else None,
        total_cost=_round(total_cost, 6),
        avg_first_tool_call_s=_round(avg_first_tool_call_s),
        avg_first_thinking_token_s=_round(avg_first_thinking_token_s),
        avg_first_answer_token_s=_round(avg_first_answer_token_s),
        tool_calls_sum=calls,
        tool_success_sum=success,
        overall_tool_success_rate=overall_rate,
        error_trace_count=int(error_trace_count or 0),
        cache_hit_rate=None,
        environments=environments,
    )


@router.get("/trends", response_model=MetricsTrendResponse)
async def get_metrics_trends(
    environment: str | None = Query(None),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    bucket: str = Query("day", description="时间分桶粒度，传给 date_trunc"),
) -> MetricsTrendResponse:
    """按时间分桶的趋势：每桶 trace 数 / 平均延迟 / 成本 / token / 工具成功率。"""
    clauses = _trace_filters(environment, from_, to)
    T = LangfuseTraceMetricRow

    bucket_col = func.date_trunc(bucket, T.trace_timestamp).label("bucket")
    stmt = (
        select(
            bucket_col,
            func.count().label("trace_count"),
            func.avg(T.latency_s).label("avg_latency_s"),
            func.sum(T.total_cost).label("total_cost"),
            func.sum(T.total_tokens).label("total_tokens"),
            func.sum(T.tool_call_count).label("calls"),
            func.sum(T.tool_success_count).label("success"),
            # 错误数：count(*) filter (where has_error)，避免 sum(cast(bool)) 方言坑
            func.count().filter(T.has_error.is_(True)).label("error_count"),
            func.avg(T.first_tool_call_s).label("avg_first_tool_call_s"),
            func.avg(T.first_thinking_token_s).label("avg_first_thinking_token_s"),
            func.avg(T.first_answer_token_s).label("avg_first_answer_token_s"),
        )
        .group_by(bucket_col)
        .order_by(bucket_col.asc())
    )
    if clauses:
        stmt = stmt.where(*clauses)

    async with async_session_factory() as session:
        rows = (await session.execute(stmt)).all()

    buckets: list[MetricsTrendBucket] = []
    for (
        b,
        trace_count,
        avg_latency_s,
        total_cost,
        total_tokens,
        calls,
        success,
        error_count,
        avg_first_tool_call_s,
        avg_first_thinking_token_s,
        avg_first_answer_token_s,
    ) in rows:
        c = int(calls or 0)
        s = int(success or 0)
        buckets.append(
            MetricsTrendBucket(
                date=_iso(b) or "",
                trace_count=int(trace_count or 0),
                avg_latency_s=_round(avg_latency_s),
                total_cost=_round(total_cost, 6),
                total_tokens=int(total_tokens) if total_tokens is not None else None,
                tool_success_rate=_round(s / c, 4) if c > 0 else None,
                error_count=int(error_count or 0),
                avg_first_tool_call_s=_round(avg_first_tool_call_s),
                avg_first_thinking_token_s=_round(avg_first_thinking_token_s),
                avg_first_answer_token_s=_round(avg_first_answer_token_s),
            )
        )

    return MetricsTrendResponse(buckets=buckets)


@router.get("/traces", response_model=TraceListResponse)
async def list_traces(
    environment: str | None = Query(None),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    name: str | None = Query(None, description="按 trace name 模糊匹配 (ilike)"),
    has_error: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> TraceListResponse:
    """trace 列表，按 trace_timestamp 倒序分页。"""
    clauses = _trace_filters(environment, from_, to)
    T = LangfuseTraceMetricRow
    if name:
        clauses.append(T.name.ilike(f"%{name}%"))
    if has_error is not None:
        clauses.append(T.has_error.is_(has_error))

    count_stmt = select(func.count()).select_from(T)
    # 列表只需这些列（含 input 给 preview）。用 load_only 避免把大字段 output
    # （单页 20 行可达数 MB）从 DB 拉到后端再丢弃 —— 实测可将本查询 0.068s→0.007s。
    list_stmt = (
        select(T)
        .options(
            load_only(
                T.langfuse_trace_id,
                T.name,
                T.environment,
                T.trace_timestamp,
                T.latency_s,
                T.total_tokens,
                T.total_cost,
                T.tool_call_count,
                T.tool_success_rate,
                T.has_error,
                T.input,
            )
        )
        .order_by(T.trace_timestamp.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if clauses:
        count_stmt = count_stmt.where(*clauses)
        list_stmt = list_stmt.where(*clauses)

    async with async_session_factory() as session:
        total = (await session.execute(count_stmt)).scalar_one()
        rows = (await session.execute(list_stmt)).scalars().all()

    traces = [
        TraceListItem(
            langfuse_trace_id=t.langfuse_trace_id,
            name=t.name,
            environment=t.environment,
            trace_timestamp=_iso(t.trace_timestamp),
            latency_s=_float(t.latency_s),
            total_tokens=t.total_tokens,
            total_cost=_float(t.total_cost),
            tool_call_count=t.tool_call_count,
            tool_success_rate=_float(t.tool_success_rate),
            has_error=t.has_error,
            input_preview=_input_preview(t.input),
        )
        for t in rows
    ]

    return TraceListResponse(
        total=int(total or 0),
        page=page,
        page_size=page_size,
        traces=traces,
    )


@router.get("/traces/{langfuse_trace_id}", response_model=TraceDetailResponse)
async def get_trace_detail(langfuse_trace_id: str) -> TraceDetailResponse:
    """单 trace 全字段 + 其 observations 明细（按 start_time 升序，NULL 殿后）。"""
    T = LangfuseTraceMetricRow
    O = LangfuseObservationMetricRow

    async with async_session_factory() as session:
        t = (
            await session.execute(
                select(T).where(T.langfuse_trace_id == langfuse_trace_id)
            )
        ).scalar_one_or_none()
        if t is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Trace not found"
            )
        obs_rows = (
            await session.execute(
                select(O)
                .where(O.langfuse_trace_id == langfuse_trace_id)
                .order_by(O.start_time.asc().nulls_last())
            )
        ).scalars().all()

    observations = [
        ObservationDetail(
            id=o.langfuse_observation_id,
            type=o.type,
            name=o.name,
            level=o.level,
            status_message=o.status_message,
            model=o.model,
            start_time=_iso(o.start_time),
            latency_s=_float(o.latency_s),
            prompt_tokens=o.prompt_tokens,
            completion_tokens=o.completion_tokens,
            total_tokens=o.total_tokens,
            calculated_total_cost=_float(o.calculated_total_cost),
            time_to_first_token_s=_float(o.time_to_first_token_s),
            parent_observation_id=o.parent_observation_id,
            input=o.input,
            output=o.output,
        )
        for o in obs_rows
    ]

    semantic_trace = _build_semantic_trace(obs_rows)

    return TraceDetailResponse(
        langfuse_trace_id=t.langfuse_trace_id,
        name=t.name,
        environment=t.environment,
        trace_timestamp=_iso(t.trace_timestamp),
        session_id=t.session_id,
        user_id=t.user_id,
        release=t.release,
        tags=t.tags,
        latency_s=_float(t.latency_s),
        input_tokens=t.input_tokens,
        output_tokens=t.output_tokens,
        total_tokens=t.total_tokens,
        total_cost=_float(t.total_cost),
        first_tool_call_s=_float(t.first_tool_call_s),
        tool_call_count=t.tool_call_count,
        tool_success_count=t.tool_success_count,
        tool_error_count=t.tool_error_count,
        tool_success_rate=_float(t.tool_success_rate),
        tool_call_counts=t.tool_call_counts,
        first_thinking_token_s=_float(t.first_thinking_token_s),
        first_answer_token_s=_float(t.first_answer_token_s),
        cache_read_tokens=t.cache_read_tokens,
        cache_creation_tokens=t.cache_creation_tokens,
        cache_hit_rate=_float(t.cache_hit_rate),
        observation_count=t.observation_count,
        generation_count=t.generation_count,
        has_error=t.has_error,
        input=t.input,
        output=t.output,
        trace_metadata=t.trace_metadata,
        scores=t.scores,
        observations=observations,
        semantic_trace=semantic_trace,
    )


@router.post("/poll", response_model=PollResponse)
async def trigger_poll() -> PollResponse:
    """手动触发一次 Langfuse 指标轮询。run_once 可能耗时，正常 await。"""
    svc = _get_service()
    result = await svc.run_once()

    last_run_traces = None
    last_run_observations = None
    if isinstance(result, dict):
        last_run_traces = result.get("last_run_traces") or result.get("traces")
        last_run_observations = result.get("last_run_observations") or result.get(
            "observations"
        )
    else:
        last_run_traces = getattr(result, "last_run_traces", None)
        last_run_observations = getattr(result, "last_run_observations", None)

    # 兜底：result 不带计数时回读游标
    if last_run_traces is None or last_run_observations is None:
        async with async_session_factory() as session:
            cursor = (
                await session.execute(
                    select(LangfuseMetricsCursorRow).where(
                        LangfuseMetricsCursorRow.scope == "global"
                    )
                )
            ).scalar_one_or_none()
        if cursor is not None:
            if last_run_traces is None:
                last_run_traces = cursor.last_run_traces
            if last_run_observations is None:
                last_run_observations = cursor.last_run_observations

    return PollResponse(
        status="ok",
        last_run_traces=last_run_traces,
        last_run_observations=last_run_observations,
    )


@router.get("/poll/status", response_model=PollStatusResponse)
async def get_poll_status() -> PollStatusResponse:
    """读单例游标 scope='global' 的轮询状态；无行则返回全默认。"""
    async with async_session_factory() as session:
        cursor = (
            await session.execute(
                select(LangfuseMetricsCursorRow).where(
                    LangfuseMetricsCursorRow.scope == "global"
                )
            )
        ).scalar_one_or_none()

    if cursor is None:
        return PollStatusResponse()

    return PollStatusResponse(
        status=cursor.status,
        last_polled_at=_iso(cursor.last_polled_at),
        last_window_start=_iso(cursor.last_window_start),
        last_window_end=_iso(cursor.last_window_end),
        traces_synced_total=cursor.traces_synced_total,
        observations_synced_total=cursor.observations_synced_total,
        last_run_traces=cursor.last_run_traces,
        last_run_observations=cursor.last_run_observations,
        consecutive_failures=cursor.consecutive_failures,
        last_error=cursor.last_error,
    )


# --------------------------------------------------------------------------- #
# 导入到备选数据集：把选中的 trace 连同答案 / 思维链 / 工具链 / 来源快照落库
# --------------------------------------------------------------------------- #
def _text_from_value(value) -> str:
    """从 trace/observation 的 input/output 提取可读文本。

    依次尝试：字符串原文 → content blocks 的 text 拼接 → messages 里最后一条
    user/assistant 文本 → 顶层 input/output/text/answer 字段。取不到返回 ""。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        blocks = _obs_content_blocks(value)
        return _text_from_blocks(blocks, "text")
    if isinstance(value, dict):
        # content blocks（Anthropic / LangGraph message 结构）
        blocks = _obs_content_blocks(value)
        txt = _text_from_blocks(blocks, "text")
        if txt:
            return txt
        # messages 列表：取最后一条带文本的消息
        messages = value.get("messages")
        if isinstance(messages, list):
            for msg in reversed(messages):
                if isinstance(msg, dict):
                    mtxt = _text_from_value(msg.get("content"))
                    if mtxt:
                        return mtxt
        for key in ("input", "question", "query", "output", "text", "answer", "result"):
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _question_from_trace_input(trace_input) -> str:
    """从 trace.input 提取用户问题：优先 messages 里最后一条 user，回退整体文本。"""
    if isinstance(trace_input, dict):
        messages = trace_input.get("messages")
        if isinstance(messages, list):
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "user":
                    txt = _text_from_value(msg.get("content"))
                    if txt:
                        return txt
    return _text_from_value(trace_input)


class ImportCandidatesRequest(BaseModel):
    trace_ids: list[str]
    dataset_name: str | None = None  # 备选数据集名；空则用 trace name / "langfuse"
    project_id: str | None = None    # 目标项目（candidate_cases.project_id）
    category: str | None = None      # 自由文本类别名（candidate_cases.category）


@router.post("/import-candidates")
async def import_traces_to_candidates(req: ImportCandidatesRequest) -> dict:
    """把选中的 Langfuse trace 导入备选数据集（candidate_cases）。

    每条 trace：question=trace.input 里的用户问题，answer=trace.output 文本，
    并把答案 / 思维链 steps / 工具链 tool_calls / 来源（trace_id、environment）
    快照写入 ``extra_metadata``，供后续评测复用。question 为空的 trace 跳过。
    有答案→status=ready，否则 pending。
    """
    if not req.trace_ids:
        raise HTTPException(status_code=400, detail="未选择任何 trace")

    T = LangfuseTraceMetricRow
    O = LangfuseObservationMetricRow
    imported = 0
    skipped = 0
    imported_at = datetime.now(timezone.utc).isoformat()

    async with async_session_factory() as session:
        for trace_id in req.trace_ids:
            t = (
                await session.execute(select(T).where(T.langfuse_trace_id == trace_id))
            ).scalar_one_or_none()
            if t is None:
                skipped += 1
                continue

            question = _question_from_trace_input(t.input)
            if not question:
                skipped += 1
                continue
            answer = _text_from_value(t.output)

            obs_rows = (
                await session.execute(
                    select(O)
                    .where(O.langfuse_trace_id == trace_id)
                    .order_by(O.start_time.asc().nulls_last())
                )
            ).scalars().all()
            semantic = _build_semantic_trace(obs_rows) or {}

            extra_metadata = {
                "source": "langfuse_trace",
                "langfuse_trace_id": trace_id,
                "environment": t.environment,
                "trace_name": t.name,
                "imported_at": imported_at,
            }
            if semantic.get("steps"):
                extra_metadata["steps"] = semantic["steps"]
            if semantic.get("tool_calls"):
                extra_metadata["tool_calls"] = semantic["tool_calls"]

            dataset_name = (req.dataset_name or "").strip() or (t.name or "langfuse")
            session.add(CandidateCaseRow(
                project_id=req.project_id or None,
                category=(req.category or "").strip() or None,
                dataset_name=dataset_name,
                source="langfuse_trace",
                question=question,
                answer=answer or None,
                extra_metadata=extra_metadata,
                status="ready" if answer else "pending",
            ))
            imported += 1

        await session.commit()

    await log_audit(
        "candidate", "import-langfuse", "create",
        details={"imported": imported, "skipped": skipped, "trace_ids": req.trace_ids[:10]},
    )
    return {"imported": imported, "skipped": skipped}
