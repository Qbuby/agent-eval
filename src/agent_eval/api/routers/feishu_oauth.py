"""飞书 user OAuth 回调端点（公开、无 JWT）。

飞书授权后浏览器 302 跳回本端点，带 ``code`` + ``state`` 两个 query 参数。
浏览器顶层导航带不了 Authorization 头，故本 router **不挂任何鉴权依赖**
（照 img_proxy 的公开 router 先例）。身份靠 ``state`` 里的签名 JWT 唯一确定
——攻击者无法伪造签名，故无法把授权结果绑到受害者账号；code 本身 5 分钟
单次使用兜底重放。

流程：校验 state（签名 + 未过期）→ httpx 打 v2 端点用 code 换 token →
按 state 里签出的可信 user_id 加密落库 → 回一个 HTML 落地页让用户回飞书。
不打印 code/token/secret 到日志。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from agent_eval.auth.security import verify_oauth_state
from agent_eval.db import async_session_factory
from agent_eval.db_models.repository import Repository
from agent_eval.evaluation.crypto import CryptoUnavailable
from agent_eval.feishu import oauth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/feishu", tags=["feishu-oauth"])


def _page(msg: str, *, ok: bool) -> HTMLResponse:
    """极简自包含 HTML 落地页（无模板引擎），成功绿/失败红。"""
    color = "#16a34a" if ok else "#dc2626"
    html = (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>飞书授权</title></head>"
        '<body style="font-family:system-ui,-apple-system,sans-serif;display:flex;'
        'height:100vh;margin:0;align-items:center;justify-content:center;background:#f8fafc">'
        f'<div style="text-align:center"><h2 style="color:{color}">{msg}</h2>'
        '<p style="color:#64748b">可以关闭本页面，回到飞书继续对话。</p></div>'
        "</body></html>"
    )
    return HTMLResponse(html, status_code=200 if ok else 400)


@router.get("/oauth/callback")
async def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if error or not code or not state:
        return _page(f"授权未完成：{error or '缺少 code/state'}", ok=False)

    verified = verify_oauth_state(state)
    if verified is None:
        return _page("授权链接已失效或被篡改，请重新发起授权。", ok=False)
    user_id, open_id = verified

    try:
        token_data = await oauth.exchange_code_for_token(code)
    except oauth.OAuthError as e:
        # e 的消息来自飞书 error_description，不含 code/secret；仍不打印原始 body。
        logger.warning("feishu oauth exchange failed for user %s", user_id)
        return _page(f"换取令牌失败：{e}", ok=False)

    try:
        async with async_session_factory() as session:
            repo = Repository(session)
            user = await repo.get_user_by_id(user_id)
            if user is None:
                return _page("找不到对应账号，请重新在飞书里发起授权。", ok=False)
            await oauth.persist_tokens(
                repo,
                user_id=user_id,
                open_id=open_id or user.feishu_open_id,
                tenant_id=user.tenant_id,
                token_data=token_data,
            )
            await session.commit()
    except CryptoUnavailable:
        logger.error("feishu oauth: SECURITY_FERNET_KEY not configured; cannot store token")
        return _page("服务端未配置加密密钥，无法安全存储授权，请联系管理员。", ok=False)
    except Exception:  # noqa: BLE001
        logger.exception("feishu oauth: failed to persist tokens for user %s", user_id)
        return _page("保存授权信息时出错，请稍后重试。", ok=False)

    return _page("授权成功！", ok=True)
