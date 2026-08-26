from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from agent_eval.auth.security import create_access_token
from agent_eval.config import settings
from agent_eval.omniagent_runtime.policy import DEFAULT_POLICY, clamp_budgets
from agent_eval.omniagent_runtime.security import (
    AUDIENCE,
    ISSUER,
    TOKEN_TYPE,
    ExecutionTokenError,
    canonical_digest,
    decode_execution_token,
    enabled_execution_tenants,
    execution_tenant_allowlist,
    mint_execution_token,
)
from agent_eval.omniagent_runtime.state import (
    ACTION_TRANSITIONS,
    JOB_TRANSITIONS,
    InvalidTransition,
    require_transition,
)


@pytest.fixture
def execution_settings(monkeypatch: pytest.MonkeyPatch) -> str:
    secret = "execution-test-secret-that-is-not-used-by-browser-jwts"
    monkeypatch.setattr(settings.omniagent, "execution_enabled", True)
    monkeypatch.setattr(settings.omniagent, "execution_secret_key", secret)
    monkeypatch.setattr(settings.omniagent, "execution_token_ttl_seconds", 300)
    monkeypatch.setattr(settings.omniagent, "execution_tenant_allowlist", "")
    return secret


def _identity() -> dict[str, uuid.UUID]:
    return {
        "user_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "message_id": uuid.uuid4(),
    }


def _claims(identity: dict[str, uuid.UUID], **overrides: object) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "type": TOKEN_TYPE,
        "sub": str(identity["user_id"]),
        "tenant_id": str(identity["tenant_id"]),
        "role": "admin",
        "session_id": str(identity["session_id"]),
        "message_id": str(identity["message_id"]),
        "scopes": ["data:query"],
        "budgets": DEFAULT_POLICY.budgets(),
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return claims


def test_execution_token_round_trip_forces_non_superadmin(execution_settings: str) -> None:
    identity = _identity()
    settings.omniagent.execution_tenant_allowlist = str(identity["tenant_id"])
    token = mint_execution_token(
        **identity,
        role="superadmin",
        scopes=["data:query", "data:query"],
        budgets={"data_queries": 2},
    )

    principal = decode_execution_token(token)

    assert principal.user_id == identity["user_id"]
    assert principal.tenant_id == identity["tenant_id"]
    assert principal.session_id == identity["session_id"]
    assert principal.message_id == identity["message_id"]
    assert principal.role == "superadmin"
    assert principal.is_superadmin is False
    assert principal.scopes == frozenset({"data:query"})
    assert principal.budgets["data_queries"] == 2


def test_browser_access_token_is_not_an_execution_token(
    execution_settings: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _identity()
    settings.omniagent.execution_tenant_allowlist = str(identity["tenant_id"])
    monkeypatch.setattr(settings.omniagent, "execution_secret_key", settings.auth.secret_key)
    browser_token = create_access_token(
        identity["user_id"], "admin", tenant_id=identity["tenant_id"]
    )

    with pytest.raises(ExecutionTokenError, match="invalid or expired"):
        decode_execution_token(browser_token)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"iss": "other-service"}, "invalid or expired"),
        ({"aud": "other-audience"}, "invalid or expired"),
        ({"type": "access"}, "wrong token type"),
    ],
)
def test_execution_token_rejects_wrong_identity_contract(
    execution_settings: str, overrides: dict[str, object], message: str
) -> None:
    token = jwt.encode(_claims(_identity(), **overrides), execution_settings, algorithm="HS256")

    with pytest.raises(ExecutionTokenError, match=message):
        decode_execution_token(token)


def test_execution_principal_rejects_missing_scope(execution_settings: str) -> None:
    identity = _identity()
    settings.omniagent.execution_tenant_allowlist = str(identity["tenant_id"])
    principal = decode_execution_token(
        mint_execution_token(**identity, role="user", scopes=["data:search"])
    )

    principal.require_scope("data:search")
    with pytest.raises(ExecutionTokenError, match="missing scope: data:query"):
        principal.require_scope("data:query")


def test_execution_tenant_allowlist_controls_mint_and_revokes_existing_token(
    execution_settings: str,
) -> None:
    identity = _identity()
    other_tenant = uuid.uuid4()

    settings.omniagent.execution_tenant_allowlist = str(other_tenant)
    with pytest.raises(ExecutionTokenError, match="disabled for this tenant"):
        mint_execution_token(**identity, role="user", scopes=["data:query"])

    settings.omniagent.execution_tenant_allowlist = str(identity["tenant_id"])
    token = mint_execution_token(**identity, role="user", scopes=["data:query"])
    assert decode_execution_token(token).tenant_id == identity["tenant_id"]

    settings.omniagent.execution_tenant_allowlist = str(other_tenant)
    with pytest.raises(ExecutionTokenError, match="no longer enabled"):
        decode_execution_token(token)


def test_execution_tenant_allowlist_rejects_invalid_configuration(
    execution_settings: str,
) -> None:
    identity = _identity()
    settings.omniagent.execution_tenant_allowlist = "not-a-uuid"
    with pytest.raises(ExecutionTokenError, match="invalid UUID"):
        mint_execution_token(**identity, role="user", scopes=["data:query"])


def test_execution_tenant_allowlist_is_deduplicated_and_fail_closed(
    execution_settings: str,
) -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()
    settings.omniagent.execution_tenant_allowlist = f" {first}, {second},{first},,"

    assert execution_tenant_allowlist() == frozenset({first, second})

    settings.omniagent.execution_tenant_allowlist = ""
    assert execution_tenant_allowlist() == frozenset()

    settings.omniagent.execution_tenant_allowlist = str(first)
    settings.omniagent.execution_enabled = False
    assert enabled_execution_tenants() == frozenset()


def test_requested_budgets_can_only_tighten_server_policy() -> None:
    effective = clamp_budgets(
        {
            "axi_calls": DEFAULT_POLICY.axi_calls_per_turn + 100,
            "data_queries": 3,
            "subagents": -5,
            "model_tokens": "invalid",
            "foreground_seconds": 30,
            "unknown": 999,
        }
    )

    assert effective == {
        "axi_calls": DEFAULT_POLICY.axi_calls_per_turn,
        "data_queries": 3,
        "subagents": 0,
        "model_tokens": DEFAULT_POLICY.model_tokens_per_turn,
        "foreground_seconds": 30,
    }


def test_canonical_digest_is_stable_across_object_key_order() -> None:
    first = {"capability": "memory.save", "arguments": {"title": "标题", "content": "内容"}}
    second = {"arguments": {"content": "内容", "title": "标题"}, "capability": "memory.save"}

    assert canonical_digest(first) == canonical_digest(second)
    assert canonical_digest(first) != canonical_digest({**first, "capability": "memory.delete"})


@pytest.mark.parametrize(
    ("transitions", "terminal"),
    [
        (JOB_TRANSITIONS, "succeeded"),
        (JOB_TRANSITIONS, "failed"),
        (JOB_TRANSITIONS, "cancelled"),
        (ACTION_TRANSITIONS, "succeeded"),
        (ACTION_TRANSITIONS, "denied"),
        (ACTION_TRANSITIONS, "expired"),
    ],
)
def test_terminal_states_are_irreversible(transitions: object, terminal: str) -> None:
    with pytest.raises(InvalidTransition, match=f"invalid transition: {terminal} ->"):
        require_transition(transitions, terminal, "queued")

def test_infrastructure_recovery_retries_only_with_budget() -> None:
    from agent_eval.omniagent_runtime.jobs import infrastructure_recovery_state

    now = datetime.now(timezone.utc)
    assert infrastructure_recovery_state(
        attempt_count=1, max_attempts=3, expires_at=None, now=now
    ) == "queued"
    assert infrastructure_recovery_state(
        attempt_count=3, max_attempts=3, expires_at=None, now=now
    ) == "failed"
    assert infrastructure_recovery_state(
        attempt_count=1,
        max_attempts=3,
        expires_at=now - timedelta(seconds=1),
        now=now,
    ) == "expired"
