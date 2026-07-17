"""飞书 user OAuth（授权码模式）—— 换取/刷新/持久化 user_access_token。

为什么走 user OAuth 而非 app 身份：Bitable 要读写用户**私人**多维表格，app
tenant_access_token 只能访问 app 被显式加为协作者的表；user_access_token 以用户
本人身份访问，覆盖其可见的所有表，无需逐表授权。

为什么用 httpx 打 v2 REST 而非 lark-oapi SDK：SDK 的 authen.v1.access_token 对应
旧 v1 端点（body {grant_type, code} + 需 app_access_token header），与本模块要的
v2 语义（client_id/client_secret + offline_access → refresh_token）不是同一套。
v2 单一端点同时覆盖换取与刷新、直接返回 refresh_token，契约更清晰。

铁律：
- 要拿到 refresh_token，authorize 的 scope 必须含 ``offline_access``（见 config
  的 oauth_scopes 默认值）。否则 token 端点不返回 refresh_token，过期即需重授权。
- refresh_token **单次使用**：每次刷新返回新的 refresh_token，旧的立即失效，故
  persist_tokens 每次整条替换 access + refresh + 两个过期时刻。
- 绝不把 code / token / client_secret 打进日志。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from agent_eval.auth.security import sign_oauth_state
from agent_eval.config import settings
from agent_eval.evaluation.crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

# 授权页与 token 端点的 host 不同（recon 结论）：授权页走 accounts.*，token 端点
# 走 open.*。抽成常量便于真机调整（海外 Lark 换 accounts.larksuite.com /
# open.larksuite.com）。
_AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"

# access_token 提前判过期的余量（秒）：临界期返回旧 token 可能在下游调用时刚好过期。
_EXPIRY_SKEW = 120


class OAuthError(RuntimeError):
    """OAuth 换取/刷新失败，消息可回显给用户（不含 secret/token）。"""


# ────────────────────────────────────────────────────────────────────────
# 发起授权
# ────────────────────────────────────────────────────────────────────────


def build_authorize_url(user_id: uuid.UUID, open_id: str) -> str:
    """拼飞书授权页 URL。state 用签名 JWT（含 user_id/open_id），回调侧校验签名
    即可可信地把授权结果绑回发起人，无需服务端存 state。"""
    state = sign_oauth_state(user_id, open_id)
    q = urlencode(
        {
            "client_id": settings.feishu.app_id,
            "redirect_uri": settings.feishu.oauth_redirect_uri,
            "response_type": "code",
            "scope": settings.feishu.oauth_scopes,
            "state": state,
        }
    )
    return f"{_AUTHORIZE_URL}?{q}"


async def request_authorization(user_id: uuid.UUID, open_id: str) -> str:
    """给用户发一张带授权链接的飞书卡片，返回给编排层的提示文案。"""
    url = build_authorize_url(user_id, open_id)
    try:
        from agent_eval.feishu.service import get_service

        await get_service().send_card(
            open_id,
            "访问你的多维表格需要一次授权。请点击下面的链接完成授权"
            f"（5 分钟内有效），然后回来重试：\n{url}",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to send feishu authorization card: %s", e)
        return f"请点击以下链接完成飞书授权后重试（5 分钟内有效）：\n{url}"
    return "已给你发送授权链接，请在飞书里点击完成授权后再重试该操作。"


# ────────────────────────────────────────────────────────────────────────
# token 端点（httpx v2 REST）
# ────────────────────────────────────────────────────────────────────────


async def _post_token(body: dict[str, Any]) -> dict[str, Any]:
    """POST v2 token 端点，返回 data；失败抛 OAuthError（不回显 body 明文）。"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(_TOKEN_URL, json=body)
    except httpx.HTTPError as e:
        raise OAuthError(f"连接飞书授权服务失败：{type(e).__name__}") from e

    try:
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        raise OAuthError(f"飞书授权服务返回异常（HTTP {resp.status_code}）") from e

    # v2 端点成功 code==0；部分成功场景 code 缺省，故也接受有 access_token 的情况。
    if data.get("code") not in (0, None) or "access_token" not in data:
        # 只回显 error/description，绝不带 token/secret。
        msg = data.get("error_description") or data.get("error") or f"code={data.get('code')}"
        raise OAuthError(str(msg))
    return data


async def exchange_code_for_token(code: str) -> dict[str, Any]:
    """authorization_code 换 user_access_token。"""
    return await _post_token(
        {
            "grant_type": "authorization_code",
            "client_id": settings.feishu.app_id,
            "client_secret": settings.feishu.app_secret,
            "code": code,
            "redirect_uri": settings.feishu.oauth_redirect_uri,
        }
    )


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """用 refresh_token 换新 token（同一端点，grant_type 区分）。"""
    return await _post_token(
        {
            "grant_type": "refresh_token",
            "client_id": settings.feishu.app_id,
            "client_secret": settings.feishu.app_secret,
            "refresh_token": refresh_token,
        }
    )


# ────────────────────────────────────────────────────────────────────────
# 持久化 + 取用（加密在此层做）
# ────────────────────────────────────────────────────────────────────────


async def persist_tokens(
    repo: Any,
    *,
    user_id: uuid.UUID,
    open_id: str | None,
    tenant_id: uuid.UUID,
    token_data: dict[str, Any],
) -> None:
    """把 token 响应加密落库（upsert）。refresh_token 单次使用，整条替换。

    调用方负责 commit（照 evaluator_providers router 范式）。encrypt_secret 在
    无 fernet_key 时抛 CryptoUnavailable，由调用方转成用户可见错误。
    """
    now = datetime.now(timezone.utc)
    expires_in = int(token_data.get("expires_in") or 0)
    refresh_expires_in = token_data.get("refresh_token_expires_in")

    await repo.upsert_feishu_oauth_token(
        user_id,
        open_id=open_id,
        tenant_id=tenant_id,
        access_token_encrypted=encrypt_secret(token_data["access_token"]),
        refresh_token_encrypted=encrypt_secret(token_data.get("refresh_token", "")),
        access_token_expires_at=(now + timedelta(seconds=expires_in)) if expires_in else None,
        refresh_token_expires_at=(
            now + timedelta(seconds=int(refresh_expires_in))
            if refresh_expires_in
            else None
        ),
        scope=token_data.get("scope"),
    )


async def get_valid_user_token(user_id: uuid.UUID) -> str | None:
    """返回可用的 user_access_token；过期则用 refresh_token 刷新并回写。

    返回 None 表示：从未授权 / refresh_token 也失效 —— 上层据此触发重新授权。
    自带 session（后台无请求上下文调用），刷新成功会自行 commit。
    """
    from agent_eval.db import async_session_factory
    from agent_eval.db_models.repository import Repository

    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.get_feishu_oauth_token(user_id)
        if row is None:
            return None

        now = datetime.now(timezone.utc)
        access = decrypt_secret(row.access_token_encrypted)
        if (
            access
            and row.access_token_expires_at
            and row.access_token_expires_at > now + timedelta(seconds=_EXPIRY_SKEW)
        ):
            return access

        # access 过期/缺失 → 尝试刷新
        refresh = decrypt_secret(row.refresh_token_encrypted)
        if not refresh or (
            row.refresh_token_expires_at and row.refresh_token_expires_at <= now
        ):
            return None  # refresh 也没了/过期 → 需重新授权

        try:
            data = await refresh_access_token(refresh)
        except OAuthError as e:
            logger.info("feishu token refresh failed for user %s: %s", user_id, e)
            return None

        await persist_tokens(
            repo,
            user_id=user_id,
            open_id=row.open_id,
            tenant_id=row.tenant_id,
            token_data=data,
        )
        await session.commit()
        return data["access_token"]
