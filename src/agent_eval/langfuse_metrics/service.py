"""Langfuse 指标轮询服务（asyncio 后台任务 + 幂等 upsert）。

进程内常驻单任务，周期（默认 24h）拉取近 30 天窗口内若干 environment 的
trace + observations，调 compute.py 算指标，按业务键幂等 upsert 进
``langfuse_trace_metrics`` / ``langfuse_observation_metrics``，并清理窗口外旧数据。
轮询状态 / 游标记录在 ``langfuse_metrics_cursors`` 的单例行（scope="global"）。

asyncio 范式与 scheduler/service.py 一致：start 起一个 create_task，stop
cancel 后 gather(return_exceptions=True)。

写入特别说明：本服务跑在后台，无租户上下文（ContextVar 为 None）。trace/obs
两表的 upsert 走 SQLAlchemy Core 的 pg_insert，**不触发** ORM 的租户盖章监听
器，所以每行必须显式带上 ``tenant_id=INTERNAL_TENANT_ID``，否则会写入 NULL。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agent_eval.config_service import config_service
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    LangfuseMetricsCursorRow,
    LangfuseObservationMetricRow,
    LangfuseTraceMetricRow,
)
from agent_eval.db_models.tenant_context import INTERNAL_TENANT_ID
from agent_eval.langfuse_metrics.client import LangfuseMetricsClient
from agent_eval.langfuse_metrics.compute import _parse_dt, compute_trace_metrics

logger = logging.getLogger(__name__)

# 拉取的目标环境列表默认值（可被 config 覆盖，见 _get_environments）。
ENVIRONMENTS = ["saas-prod", "xinchai-prod", "smartlink-hc-dev"]

# 默认轮询间隔（秒）与回看天数（首次回填窗口 + 数据保留期），均可被 config 覆盖。
DEFAULT_INTERVAL_SECONDS = 86400
DEFAULT_LOOKBACK_DAYS = 30

# 增量窗口重叠量（秒）：每轮从「上次窗口结束 − 该重叠」起拉，吸收 trace 的
# cost/score 异步补算（创建后才回填）造成的边界遗漏。15min 足够覆盖补算延迟。
_INCREMENTAL_OVERLAP_SECONDS = 900

# config key（可在系统配置页编辑，带 DEFAULT_CONFIGS 默认值 + 热更新）。
_CFG_INTERVAL = "langfuse_metrics.poll_interval_seconds"
_CFG_LOOKBACK = "langfuse_metrics.lookback_days"
_CFG_ENVIRONMENTS = "langfuse_metrics.environments"

# observation 批量 upsert 单批行数上限，避免单条 SQL 过大。
_OBS_BATCH_SIZE = 500


def _utcnow() -> datetime:
    """当前 UTC aware 时间。"""
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    """把 DB 取出的时间统一成 UTC aware；naive 视为 UTC。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _jsonable(v):
    """保证值能塞进 JSONB 列。

    Langfuse 返回的 input/output/metadata/scores 基本已是 json 兼容
    （dict/list/str/num/bool/None），原样返回；遇到非常规类型兜底转 str。
    """
    if v is None or isinstance(v, (dict, list, str, int, float, bool)):
        return v
    return str(v)


class LangfuseMetricsService:
    """Langfuse 指标轮询服务。

    懒构造 client：未配置 Langfuse 时 start 直接 warning 返回不起任务，避免
    实例化阶段就因缺凭据崩溃。
    """

    def __init__(self, client=None, interval_seconds: int | None = None, lookback_days: int | None = None):
        # client 懒构造：None 时在 start 才 from_settings
        self._client = client
        # 显式传参覆盖默认；否则用默认常量，start 时再从 config 读取覆盖。
        self._interval = interval_seconds if interval_seconds is not None else DEFAULT_INTERVAL_SECONDS
        self._lookback = lookback_days if lookback_days is not None else DEFAULT_LOOKBACK_DAYS
        self._task: asyncio.Task | None = None
        self._running = False
        self._config_listener_registered = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """启动后台轮询任务。未配置 Langfuse 或已在运行则不重复启动。"""
        conn = await config_service.get_langfuse_connection()
        if not conn["configured"]:
            logger.warning("langfuse-metrics: 未配置 Langfuse（host/key 缺失），轮询不启动")
            return
        if self._running:
            return
        if self._client is None:
            self._client = LangfuseMetricsClient.from_connection(conn)
        # 从 config 读取间隔 / 回看天数（覆盖默认），并注册热更新监听（仅一次）。
        self._interval = await self._get_interval()
        self._lookback = await self._get_lookback()
        if not self._config_listener_registered:
            config_service.on_change(self._on_config_change)
            self._config_listener_registered = True
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(), name="langfuse-metrics-poll")
        logger.info(
            "langfuse-metrics: 轮询已启动（间隔 %ds，回看 %d 天）",
            self._interval, self._lookback,
        )

    async def _get_interval(self) -> int:
        """读轮询间隔（秒），config 缺失或非法时回退默认。"""
        val = await config_service.get(_CFG_INTERVAL)
        try:
            iv = int(val)
            if iv >= 60:
                return iv
        except (TypeError, ValueError):
            pass
        return DEFAULT_INTERVAL_SECONDS

    async def _get_lookback(self) -> int:
        """读首次回填 / 数据保留天数，config 缺失或非法时回退默认。"""
        val = await config_service.get(_CFG_LOOKBACK)
        try:
            lb = int(val)
            if lb >= 1:
                return lb
        except (TypeError, ValueError):
            pass
        return DEFAULT_LOOKBACK_DAYS

    async def _get_environments(self) -> list[str]:
        """读拉取的目标环境列表。config 存逗号分隔串或 list；缺失回退默认。"""
        val = await config_service.get(_CFG_ENVIRONMENTS)
        if isinstance(val, list):
            envs = [str(e).strip() for e in val if str(e).strip()]
            if envs:
                return envs
        if isinstance(val, str) and val.strip():
            envs = [e.strip() for e in val.split(",") if e.strip()]
            if envs:
                return envs
        return list(ENVIRONMENTS)

    def _on_config_change(self, key: str, value: Any) -> None:
        """config 热更新：间隔变化即时生效（下一轮 sleep 用新值）。"""
        if key == _CFG_INTERVAL:
            try:
                iv = int(value)
                if iv >= 60:
                    self._interval = iv
                    logger.info("langfuse-metrics: 轮询间隔更新为 %ds", iv)
            except (TypeError, ValueError):
                pass
        elif key == _CFG_LOOKBACK:
            try:
                lb = int(value)
                if lb >= 1:
                    self._lookback = lb
                    logger.info("langfuse-metrics: 回看天数更新为 %d", lb)
            except (TypeError, ValueError):
                pass

    async def stop(self) -> None:
        """停止后台轮询任务。"""
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        logger.info("langfuse-metrics: 轮询已停止")

    async def _poll_loop(self) -> None:
        """轮询主循环：到点（距上次 ≥ 间隔，含进程重启补跑）才 run_once。

        单轮异常被吞掉（已记日志 + _mark_failure），不让循环崩；睡眠时间按距
        上次轮询的剩余间隔动态计算，下限 60s 防止空转。
        """
        try:
            while self._running:
                cursor = await self._load_cursor()
                now = _utcnow()
                last = _as_aware(cursor["last_polled_at"])
                due = last is None or (now - last).total_seconds() >= self._interval
                if due:
                    try:
                        await self.run_once()
                    except Exception:
                        logger.exception("langfuse-metrics: run_once 失败")

                # 重新读游标算剩余间隔（run_once 成功会刷新 last_polled_at）
                cursor = await self._load_cursor()
                last = _as_aware(cursor["last_polled_at"])
                if last is None:
                    sleep_s = self._interval
                else:
                    elapsed = (_utcnow() - last).total_seconds()
                    sleep_s = max(60, min(self._interval, self._interval - elapsed))
                await asyncio.sleep(sleep_s)
        except asyncio.CancelledError:
            pass

    async def run_once(self) -> dict:
        """执行一轮**增量**拉取 + upsert + 清理。返回本轮计数。

        增量窗口：起点 = 游标 last_window_end − 重叠量（吸收 cost/score 异步
        补算造成的边界遗漏）；首次（无游标 last_window_end）则回退 lookback 天
        做全量回填。终点 = now。逐 environment 翻页拉 trace；单 trace 失败只
        warning 跳过不影响整体；整轮异常落 _mark_failure 后 re-raise。

        清理仍按 lookback 天保留期删旧数据（与增量起点解耦）。
        """
        await self._set_status("running")
        try:
            window_end = _utcnow()
            # 增量起点：上次窗口结束回退重叠量；无则首次全量回填 lookback 天。
            cursor = await self._load_cursor()
            last_end = _as_aware(cursor.get("last_window_end"))
            if last_end is not None:
                window_start = last_end - timedelta(seconds=_INCREMENTAL_OVERLAP_SECONDS)
            else:
                window_start = window_end - timedelta(days=self._lookback)
            retain_start = window_end - timedelta(days=self._lookback)
            environments = await self._get_environments()
            n_tr = 0
            n_ob = 0

            for env in environments:
                async for trace in self._client.iter_traces(env, window_start, window_end):
                    try:
                        observations = await self._client.get_trace_observations(trace["id"])
                        metrics = compute_trace_metrics(trace, observations)
                        await self._upsert_trace(trace, metrics, env)
                        await self._upsert_observations(trace, observations, env)
                        n_tr += 1
                        n_ob += len(observations)
                    except Exception as e:
                        logger.warning(
                            "langfuse-metrics: trace %s 处理失败，跳过：%s",
                            trace.get("id"), e,
                        )
                        continue

            await self._cleanup_old(retain_start)
            await self._mark_success(window_start, window_end, n_tr, n_ob)
            logger.info("langfuse-metrics: 本轮完成 traces=%d observations=%d", n_tr, n_ob)
            return {"last_run_traces": n_tr, "last_run_observations": n_ob}
        except Exception as e:
            await self._mark_failure(str(e))
            raise

    async def _upsert_trace(self, trace: dict, metrics: dict, env: str) -> None:
        """按 langfuse_trace_id 幂等 upsert 单条 trace 指标。"""
        row = {
            "langfuse_trace_id": trace["id"],
            "tenant_id": INTERNAL_TENANT_ID,  # Core 写入不触发盖章监听器，必须显式带
            "environment": env,
            "name": trace.get("name"),
            "trace_timestamp": _parse_dt(trace.get("timestamp")),
            "session_id": trace.get("sessionId"),
            "user_id": trace.get("userId"),
            "release": trace.get("release"),
            "tags": trace.get("tags"),
            "input": _jsonable(trace.get("input")),
            "output": _jsonable(trace.get("output")),
            "trace_metadata": _jsonable(trace.get("metadata")),
            "scores": _jsonable(trace.get("scores")),
            "raw_synced_at": _utcnow(),
            **metrics,
        }
        stmt = pg_insert(LangfuseTraceMetricRow).values(**row)
        # 冲突时更新除主键 / 业务键 / created_at 外的所有列，并刷新 updated_at
        update_cols = {k: v for k, v in row.items() if k != "langfuse_trace_id"}
        update_cols["updated_at"] = _utcnow()
        stmt = stmt.on_conflict_do_update(
            index_elements=["langfuse_trace_id"], set_=update_cols
        )
        async with async_session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def _upsert_observations(self, trace: dict, observations: list[dict], env: str) -> None:
        """按 langfuse_observation_id 幂等批量 upsert observation 明细。"""
        if not observations:
            return

        trace_ts = _parse_dt(trace.get("timestamp"))
        rows = []
        for o in observations:
            usage = o.get("usageDetails") or {}
            rows.append({
                "langfuse_observation_id": o["id"],
                "tenant_id": INTERNAL_TENANT_ID,  # Core 写入需显式带租户
                "langfuse_trace_id": trace["id"],
                "environment": env,
                "trace_timestamp": trace_ts,
                "type": o.get("type"),
                "name": o.get("name"),
                "level": o.get("level"),
                "status_message": o.get("statusMessage"),
                "model": o.get("model"),
                "start_time": _parse_dt(o.get("startTime")),
                "end_time": _parse_dt(o.get("endTime")),
                "latency_s": o.get("latency"),
                "prompt_tokens": o.get("promptTokens"),
                "completion_tokens": o.get("completionTokens"),
                "total_tokens": o.get("totalTokens"),
                "usage_input": usage.get("input"),
                "usage_output": usage.get("output"),
                "usage_total": usage.get("total"),
                "calculated_total_cost": o.get("calculatedTotalCost"),
                "total_price": o.get("totalPrice"),
                "time_to_first_token_s": o.get("timeToFirstToken"),
                "completion_start_time": _parse_dt(o.get("completionStartTime")),
                "parent_observation_id": o.get("parentObservationId"),
                "obs_metadata": _jsonable(o.get("metadata")),
                "input": _jsonable(o.get("input")),
                "output": _jsonable(o.get("output")),
            })

        # 冲突更新列：除业务键外全部覆盖（明细无 updated_at 列）
        mutable_keys = [k for k in rows[0].keys() if k != "langfuse_observation_id"]

        async with async_session_factory() as session:
            for start in range(0, len(rows), _OBS_BATCH_SIZE):
                batch = rows[start:start + _OBS_BATCH_SIZE]
                stmt = pg_insert(LangfuseObservationMetricRow).values(batch)
                update_cols = {k: getattr(stmt.excluded, k) for k in mutable_keys}
                stmt = stmt.on_conflict_do_update(
                    index_elements=["langfuse_observation_id"], set_=update_cols
                )
                await session.execute(stmt)
            await session.commit()

    async def _load_cursor(self) -> dict:
        """读取 scope='global' 单例游标；不存在则创建。

        返回轻量 dict（含 last_polled_at），避免 ORM detached 取属性问题。
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(LangfuseMetricsCursorRow).where(
                    LangfuseMetricsCursorRow.scope == "global"
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = LangfuseMetricsCursorRow(
                    scope="global",
                    status="idle",
                    tenant_id=INTERNAL_TENANT_ID,
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
            return {
                "last_polled_at": row.last_polled_at,
                "last_window_end": row.last_window_end,
                "status": row.status,
                "consecutive_failures": row.consecutive_failures,
            }

    async def _set_status(self, status: str) -> None:
        """更新游标状态列（running / idle / error）。"""
        async with async_session_factory() as session:
            await session.execute(
                update(LangfuseMetricsCursorRow)
                .where(LangfuseMetricsCursorRow.scope == "global")
                .values(status=status)
            )
            await session.commit()

    async def _mark_success(
        self, window_start: datetime, window_end: datetime, n_tr: int, n_ob: int
    ) -> None:
        """记录一轮成功：刷新游标、累计计数、清零失败计数与错误。"""
        async with async_session_factory() as session:
            await session.execute(
                update(LangfuseMetricsCursorRow)
                .where(LangfuseMetricsCursorRow.scope == "global")
                .values(
                    status="idle",
                    last_polled_at=_utcnow(),
                    last_window_start=window_start,
                    last_window_end=window_end,
                    last_run_traces=n_tr,
                    last_run_observations=n_ob,
                    traces_synced_total=LangfuseMetricsCursorRow.traces_synced_total + n_tr,
                    observations_synced_total=(
                        LangfuseMetricsCursorRow.observations_synced_total + n_ob
                    ),
                    consecutive_failures=0,
                    last_error=None,
                )
            )
            await session.commit()

    async def _mark_failure(self, error: str) -> None:
        """记录一轮失败：status=error，连续失败计数 +1，截断错误信息。"""
        async with async_session_factory() as session:
            await session.execute(
                update(LangfuseMetricsCursorRow)
                .where(LangfuseMetricsCursorRow.scope == "global")
                .values(
                    status="error",
                    consecutive_failures=LangfuseMetricsCursorRow.consecutive_failures + 1,
                    last_error=error[:500],
                )
            )
            await session.commit()

    async def _cleanup_old(self, window_start: datetime) -> None:
        """删除窗口外（trace_timestamp < window_start）的 trace / observation。"""
        async with async_session_factory() as session:
            await session.execute(
                delete(LangfuseTraceMetricRow).where(
                    LangfuseTraceMetricRow.trace_timestamp < window_start
                )
            )
            await session.execute(
                delete(LangfuseObservationMetricRow).where(
                    LangfuseObservationMetricRow.trace_timestamp < window_start
                )
            )
            await session.commit()
