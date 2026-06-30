"""图片代理（内部只读）。

多轮对话样例 / Portal 答案里的图片 ``![](url)`` 直挂外链，浏览器 ``<img>`` 直连
有两类取不到：

* 阿里云 OSS（``*.oss-*.aliyuncs.com``）配了 **Referer 防盗链**：浏览器自带
  Referer → 403。服务端直拉不带 Referer → 200。
* 内网图床（``aiservice.ep-ep.com`` 等）：浏览器所在网络解析/可达不稳定，但后端
  部署环境可达。

故由后端代你去拉图、剥掉 Referer 再回流给前端。``MarkdownView`` 把 ``<img src>``
改写成 ``/api/img-proxy?url=<原始URL>`` 走这里。

**SSRF 防护**：只允许白名单 host（图片实际用到的 OSS / 内网图床域名）+ http/https，
拒绝其余一切 URL，避免被当成打内网任意地址的跳板。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Response

logger = logging.getLogger(__name__)

# ⚠️ 本 router **不挂鉴权**：图片经浏览器 `<img src>` 直连，标签请求带不了
# Authorization 头（JWT 在 axios 拦截器里加，img 标签无从携带），加 require_internal
# 会让所有图片 401。安全靠：① host 后缀白名单（拒一切非白名单域名+拒 IP 直连，
# 防 SSRF）② 只回流 image/* ③ 大小上限 ④ 不转发 Cookie/Referer。代理的都是公网/
# 内网图床上的叉车诊断图，非敏感数据。
router = APIRouter(
    prefix="/api/img-proxy",
    tags=["img-proxy"],
)

# 允许代理的 host 后缀白名单。来源：多轮样例 answer 实际出现的图片域名
# （阿里云 OSS 各 bucket + EP 内网图床）。新增图源时在此追加。
_ALLOWED_HOST_SUFFIXES: tuple[str, ...] = (
    ".oss-cn-hangzhou.aliyuncs.com",
    ".oss-accelerate.aliyuncs.com",
    "aiservice.ep-ep.com",
    "epcare.ep-ep.com",
    "ep-care.com",
)

# 允许回流的内容类型前缀（只代理图片，杜绝把代理当通用 fetch 用）。
_ALLOWED_CONTENT_TYPE = "image/"

# 单图大小上限（字节）。防止超大响应打爆内存 / 带宽。
_MAX_BYTES = 20 * 1024 * 1024

_TIMEOUT = httpx.Timeout(15.0, connect=8.0)

# 容器 DNS 对阿里云 OSS 间歇性失败时的有限重试：尝试次数 + 退避基数（秒，线性递增）。
_FETCH_ATTEMPTS = 4
_FETCH_BACKOFF = 0.4


def _host_allowed(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if not h:
        return False
    # 拒绝 IP 直连（白名单全是域名；IP 形式多为 SSRF 探测内网）。
    try:
        ipaddress.ip_address(h)
        return False
    except ValueError:
        pass
    return any(
        h == suf.lstrip(".") or h.endswith(suf)
        for suf in _ALLOWED_HOST_SUFFIXES
    )


@router.get("")
async def proxy_image(url: str = Query(..., max_length=2000)):
    """服务端拉取白名单图片并回流（不带 Referer，绕过 OSS 防盗链）。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="only http/https URLs are allowed")
    if not _host_allowed(parsed.hostname or ""):
        raise HTTPException(status_code=403, detail="host not in image proxy allowlist")

    # 容器出口 DNS（compose 配的 8.8.8.8/1.1.1.1）在国内对阿里云 OSS 域名解析
    # **间歇性失败**（``Temporary failure in name resolution``）或偶尔解析到会
    # 返回错误码的节点——同一张真实存在的图，连试几次往往前几次失败、之后成功。
    # 故对「连接/DNS 类失败」与「非 2xx」都做有限重试（跟随重定向以兼容 OSS 区域
    # 跳转）。重试全部用尽才判失败，避免把"DNS 抖动"误当"源站没图"。
    resp = None
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        for attempt in range(_FETCH_ATTEMPTS):
            try:
                r = await client.get(url, headers={"Accept": "image/*,*/*;q=0.8"})
            except httpx.HTTPError as e:
                last_err = e
                logger.info(
                    "img-proxy fetch attempt %d/%d failed url=%s err=%s",
                    attempt + 1, _FETCH_ATTEMPTS, url, type(e).__name__,
                )
                await asyncio.sleep(_FETCH_BACKOFF * (attempt + 1))
                continue
            if r.status_code == 200:
                resp = r
                break
            # 非 200：可能是 DNS 解析到错误节点的偶发结果，重试可能换到好节点。
            last_err = HTTPException(status_code=502, detail=f"upstream HTTP {r.status_code}")
            logger.info(
                "img-proxy upstream HTTP %d attempt %d/%d url=%s",
                r.status_code, attempt + 1, _FETCH_ATTEMPTS, url,
            )
            await asyncio.sleep(_FETCH_BACKOFF * (attempt + 1))

    if resp is None:
        raise HTTPException(
            status_code=502,
            detail=f"upstream image fetch failed after {_FETCH_ATTEMPTS} attempts",
        ) from (last_err if isinstance(last_err, BaseException) else None)

    content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not content_type.startswith(_ALLOWED_CONTENT_TYPE):
        raise HTTPException(status_code=415, detail="upstream is not an image")

    body = resp.content
    if len(body) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="image too large")

    return Response(
        content=body,
        media_type=content_type,
        # 代理结果可缓存，减少重复回源（内容由 url 唯一决定）。
        headers={"Cache-Control": "public, max-age=86400"},
    )
