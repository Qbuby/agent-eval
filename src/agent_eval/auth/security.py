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
