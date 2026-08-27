from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import jwt

from agent_eval.config import settings
from agent_eval.omniagent_runtime.policy import DEFAULT_POLICY, clamp_budgets

ISSUER = "agent-eval"
AUDIENCE = "omniagent-execution"
TOKEN_TYPE = "omniagent_execution"


class ExecutionTokenError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionPrincipal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    session_id: uuid.UUID
    message_id: uuid.UUID
    scopes: frozenset[str]
    budgets: dict[str, int]
    token_id: uuid.UUID
    expires_at: datetime

    @property
    def is_superadmin(self) -> bool:
        return False

    def require_scope(self, scope: str) -> None:
        if scope not in self.scopes:
            raise ExecutionTokenError(f"missing scope: {scope}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _execution_secret() -> str:
    secret = settings.omniagent.execution_secret_key.strip()
    if not secret:
        raise ExecutionTokenError("OMNIAGENT_EXECUTION_SECRET_KEY is not configured")
    return secret


def execution_tenant_allowlist() -> frozenset[uuid.UUID]:
    """Parse the fail-closed tenant rollout allowlist from server settings."""
    raw = settings.omniagent.execution_tenant_allowlist
    values: set[uuid.UUID] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.add(uuid.UUID(item))
        except ValueError as exc:
            raise ExecutionTokenError(
                "OMNIAGENT_EXECUTION_TENANT_ALLOWLIST contains an invalid UUID"
            ) from exc
    return frozenset(values)


def execution_enabled_for_tenant(tenant_id: uuid.UUID) -> bool:
    """Return whether execution is globally enabled and explicitly enabled for a tenant."""
    return tenant_id in enabled_execution_tenants()


def enabled_execution_tenants() -> frozenset[uuid.UUID]:
    """Return the current rollout set, empty whenever global execution is disabled."""
    if not settings.omniagent.execution_enabled:
        return frozenset()
    return execution_tenant_allowlist()


def mint_execution_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str,
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    scopes: Iterable[str],
    budgets: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
) -> str:
    if not execution_enabled_for_tenant(tenant_id):
        raise ExecutionTokenError("OmniAgent execution is disabled for this tenant")
    now = datetime.now(timezone.utc)
    ttl = ttl_seconds or settings.omniagent.execution_token_ttl_seconds
    ttl = max(30, min(int(ttl), 600))
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "type": TOKEN_TYPE,
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "session_id": str(session_id),
        "message_id": str(message_id),
        "scopes": sorted(set(scopes)),
        "budgets": clamp_budgets(budgets, DEFAULT_POLICY),
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
    }
    return jwt.encode(payload, _execution_secret(), algorithm="HS256")


def decode_execution_token(token: str) -> ExecutionPrincipal:
    try:
        payload = jwt.decode(
            token,
            _execution_secret(),
            algorithms=["HS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "jti", "sub", "tenant_id"]},
        )
    except (jwt.PyJWTError, ExecutionTokenError) as exc:
        raise ExecutionTokenError("invalid or expired execution token") from exc
    if payload.get("type") != TOKEN_TYPE:
        raise ExecutionTokenError("wrong token type")
    try:
        scopes = payload.get("scopes")
        if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
            raise ValueError("invalid scopes")
        tenant_id = uuid.UUID(payload["tenant_id"])
        if not execution_enabled_for_tenant(tenant_id):
            raise ExecutionTokenError("execution tenant is no longer enabled")
        budgets = clamp_budgets(payload.get("budgets"), DEFAULT_POLICY)
        return ExecutionPrincipal(
            user_id=uuid.UUID(payload["sub"]),
            tenant_id=tenant_id,
            role=str(payload.get("role") or "user"),
            session_id=uuid.UUID(payload["session_id"]),
            message_id=uuid.UUID(payload["message_id"]),
            scopes=frozenset(scopes),
            budgets=budgets,
            token_id=uuid.UUID(payload["jti"]),
            expires_at=datetime.fromtimestamp(float(payload["exp"]), timezone.utc),
        )
    except ExecutionTokenError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ExecutionTokenError("malformed execution token claims") from exc
