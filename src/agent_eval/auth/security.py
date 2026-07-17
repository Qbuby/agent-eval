from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from agent_eval.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(
    user_id: uuid.UUID, role: str, tenant_id: uuid.UUID | None = None
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.auth.access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expire,
        "type": "access",
    }
    # tenant_id 作为可选 claim：调用方（auth router 的 login/refresh）传入后
    # 写进 token，便于将来无需查库即可恢复租户上下文。可选参数保持后向兼容，
    # 现有调用点不传也能工作（租户上下文目前由 get_current_user 查库设置）。
    if tenant_id is not None:
        payload["tenant_id"] = str(tenant_id)
    return jwt.encode(payload, settings.auth.secret_key, algorithm=settings.auth.algorithm)


def create_refresh_token(user_id: uuid.UUID) -> tuple[str, datetime]:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.auth.refresh_token_expire_days)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, settings.auth.secret_key, algorithm=settings.auth.algorithm)
    return token, expire


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token, settings.auth.secret_key, algorithms=[settings.auth.algorithm]
        )
        if payload.get("type") != "access":
            return None
        return payload
    except jwt.PyJWTError:
        return None


def decode_refresh_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token, settings.auth.secret_key, algorithms=[settings.auth.algorithm]
        )
        if payload.get("type") != "refresh":
            return None
        return payload
    except jwt.PyJWTError:
        return None


# ── 飞书 OAuth state（CSRF 防护，无存储）──────────────────────────────
# 用签名 JWT 而非内存 dict / DB 表承载授权发起态：回调端点与 bot 长连接同进程，
# 但进程重启 / 多 worker 时内存 dict 不共享；签名 state 天然跨重启、跨 worker、
# 免迁移，复用同一 HS256 + secret_key。攻击者无法伪造签名，故无法把授权结果
# 绑到受害者账号；飞书 code 本身 5 分钟单次使用兜底重放。


def sign_oauth_state(user_id: uuid.UUID, open_id: str, *, ttl_seconds: int = 300) -> str:
    """签发飞书 OAuth 授权发起态。携带发起授权的 user_id + open_id，回调校验后
    据此把换得的 token 绑到正确账号（因签名不可伪造，绑定对象可信）。"""
    payload = {
        "sub": str(user_id),
        "open_id": open_id,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        "type": "feishu_oauth_state",
    }
    return jwt.encode(payload, settings.auth.secret_key, algorithm=settings.auth.algorithm)


def verify_oauth_state(state: str) -> tuple[uuid.UUID, str] | None:
    """校验回调带回的 state。返回 (user_id, open_id)；无效 / 过期 / 类型不符 / 被篡改
    一律返回 None（回调据此给「链接失效，请重新授权」而非崩溃）。"""
    try:
        payload = jwt.decode(
            state, settings.auth.secret_key, algorithms=[settings.auth.algorithm]
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "feishu_oauth_state":
        return None
    try:
        return uuid.UUID(payload["sub"]), payload.get("open_id", "")
    except (KeyError, ValueError):
        return None
