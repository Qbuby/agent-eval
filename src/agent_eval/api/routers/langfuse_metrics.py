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

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import distinct, func, select

from agent_eval.auth.dependencies import require_internal
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    LangfuseMetricsCursorRow,
    LangfuseObservationMetricRow,
    LangfuseTraceMetricRow,
)

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
    list_stmt = (
        select(T)
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
        )
        for o in obs_rows
    ]

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
