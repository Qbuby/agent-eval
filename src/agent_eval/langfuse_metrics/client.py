"""Langfuse 公共 API 入站读取客户端。

这是「指标」侧的只读客户端：从 Langfuse 公共 API 拉 trace 列表与单 trace
详情（含 observations），喂给上层做指标聚合。复用 evaluation/langfuse_sync.py
里同款的 Basic Auth base64 构造模式与 host 处理（``host.rstrip("/")``）。

鉴权：``base64(public_key:secret_key)`` 拼成 ``Authorization: Basic ...`` 头。
凭据从 ``agent_eval.config.settings.langfuse`` 读，见 :meth:`from_settings`。

本模块只读 HTTP，不连 DB。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

# 每页请求的最大重试次数（含首次），针对 5xx / 超时
_MAX_ATTEMPTS = 3
# 指数退避基数：第 n 次失败后睡 2**(n-1) 秒 → 1s, 2s, 4s
_BACKOFF_BASE = 1.0
# 单次请求超时
_TIMEOUT = 30.0
# 翻页每页条数
_PAGE_LIMIT = 100


class LangfuseMetricsClient:
    """Langfuse 公共 API 的只读异步客户端。

    每个公开方法内部自建 ``httpx.AsyncClient``（``async with`` 进出），
    所以实例本身不持有连接，可安全跨任务复用。
    """

    def __init__(self, base: str, headers: dict):
        # base 已去尾斜杠；headers 含 Authorization Basic 头
        self._base = base
        self._headers = headers

    @classmethod
    def from_connection(cls, conn: dict) -> "LangfuseMetricsClient":
        """从连接组 dict（host/public_key/secret_key）构造客户端。

        与 evaluation/langfuse_sync.py 同款：base64 拼 ``pk:sk`` 做 Basic Auth，
        host 去尾斜杠。连接组由 ``config_service.get_langfuse_connection()``
        解析（连接预设 → env 回退）。
        """
        auth = base64.b64encode(
            f"{conn.get('public_key', '')}:{conn.get('secret_key', '')}".encode()
        ).decode()
        headers = {"Authorization": f"Basic {auth}"}
        base = (conn.get("host") or "").rstrip("/")
        return cls(base=base, headers=headers)

    @classmethod
    async def from_settings(cls) -> "LangfuseMetricsClient":
        """从生效的 Langfuse 连接预设构造客户端（异步）。

        历史名保留兼容；内部改读 ``config_service.get_langfuse_connection()``，
        即连接预设默认项，缺失时回退 env。
        """
        from agent_eval.config_service import config_service

        conn = await config_service.get_langfuse_connection()
        return cls.from_connection(conn)

    async def _get(self, c: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
        """带重试的 GET，返回解析后的 JSON dict。

        对 5xx（``HTTPStatusError`` 且 ``status_code >= 500``）与超时
        （``TimeoutException``）重试，指数退避 1s/2s/4s；4xx 直接抛不重试，
        超过最大次数也抛最后一次异常。
        """
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                r = await c.get(f"{self._base}{path}", params=params)
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                # 4xx 是确定性错误（鉴权/参数），重试无意义，直接抛
                if e.response.status_code < 500:
                    raise
                last_exc = e
                logger.warning(
                    "langfuse-metrics: GET %s 第 %d/%d 次失败 (HTTP %d)",
                    path, attempt, _MAX_ATTEMPTS, e.response.status_code,
                )
            except httpx.TimeoutException as e:
                last_exc = e
                logger.warning(
                    "langfuse-metrics: GET %s 第 %d/%d 次超时", path, attempt, _MAX_ATTEMPTS
                )

            # 还有重试机会就退避后再来；否则跳出循环抛异常
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))

        # 用尽重试仍失败
        assert last_exc is not None
        raise last_exc

    async def iter_traces(self, environment: str, from_ts: datetime, to_ts: datetime):
        """翻页拉取指定环境与时间窗内的 trace，逐个 yield trace dict。

        GET ``/api/public/traces``，按 ``meta.totalPages`` 从 page=1 翻到末页。
        响应形如 ``{"data": [...], "meta": {"page","limit","totalItems","totalPages"}}``。
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=self._headers) as c:
            page = 1
            total_pages = 1  # 拿到首页 meta 后更新
            while page <= total_pages:
                body = await self._get(
                    c,
                    "/api/public/traces",
                    params={
                        "environment": environment,
                        "fromTimestamp": from_ts.isoformat(),
                        "toTimestamp": to_ts.isoformat(),
                        "page": page,
                        "limit": _PAGE_LIMIT,
                    },
                )
                meta = body.get("meta", {})
                total_pages = meta.get("totalPages", 0) or 0
                for trace in body.get("data", []):
                    yield trace
                page += 1

    async def get_trace_observations(self, trace_id: str) -> list[dict]:
        """拉单 trace 详情，返回其 ``observations`` 数组（全字段）。

        GET ``/api/public/traces/{trace_id}``，响应是单 trace dict，含
        ``observations`` 数组。trace 不含该字段时返回空列表。
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=self._headers) as c:
            body = await self._get(c, f"/api/public/traces/{trace_id}")
            return body.get("observations", [])
