"""HTTP API for evaluator-provider credentials (LLM-judge endpoints).

A "provider" is one usable LLM API endpoint (OpenAI, Anthropic, DeepSeek,
Azure, OpenAI-compatible, custom). The configurable LLM-judge evaluators
in PR-B will reference these by id via ``params['provider_id']``.

Auth: every endpoint requires admin. Provider rows hold credentials, so
read access is restricted even on the GET side — non-admins shouldn't see
how many providers exist or what URLs they hit.

API-key handling:
  * stored fernet-encrypted (``api_key_encrypted`` BYTEA column)
  * never returned in plaintext — responses surface ``has_api_key`` /
    ``api_key_masked`` only
  * on update: ``api_key`` field omitted = keep existing, "" = clear,
    any other value = re-encrypt and replace
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from agent_eval.api.schemas import (
    ALLOWED_PROVIDER_TYPES,
    CreateEvaluatorProviderRequest,
    EvaluatorProviderResponse,
    ProviderModelsResponse,
    TestProviderResponse,
    UpdateEvaluatorProviderRequest,
)
from agent_eval.auth.dependencies import require_admin
from agent_eval.db import async_session_factory
from agent_eval.db_models.repository import Repository
from agent_eval.db_models.tables import UserRow
from agent_eval.evaluation.crypto import (
    CryptoUnavailable,
    decrypt_secret,
    encrypt_secret,
    mask_secret,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/evaluator-providers", tags=["evaluator-providers"])


def _row_to_response(row: Any) -> EvaluatorProviderResponse:
    plaintext = decrypt_secret(row.api_key_encrypted) if row.api_key_encrypted else None
    return EvaluatorProviderResponse(
        id=str(row.id),
        name=row.name,
        provider_type=row.provider_type,
        base_url=row.base_url,
        default_model=row.default_model,
        extra_config=row.extra_config or {},
        is_active=row.is_active,
        has_api_key=bool(row.api_key_encrypted),
        api_key_masked=mask_secret(plaintext) if plaintext else (
            "•" * 8 if row.api_key_encrypted else ""
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_provider_type(provider_type: str) -> None:
    if provider_type not in ALLOWED_PROVIDER_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown provider_type '{provider_type}'. "
                f"allowed: {', '.join(ALLOWED_PROVIDER_TYPES)}"
            ),
        )


@router.get("", response_model=list[EvaluatorProviderResponse])
async def list_providers(
    active_only: bool = False,
    _admin: UserRow = Depends(require_admin),
):
    async with async_session_factory() as session:
        repo = Repository(session)
        rows = await repo.list_evaluator_providers(active_only=active_only)
    return [_row_to_response(r) for r in rows]


@router.post("", response_model=EvaluatorProviderResponse)
async def create_provider(
    req: CreateEvaluatorProviderRequest,
    admin: UserRow = Depends(require_admin),
):
    _validate_provider_type(req.provider_type)

    encrypted: bytes | None = None
    if req.api_key:
        try:
            encrypted = encrypt_secret(req.api_key)
        except CryptoUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    async with async_session_factory() as session:
        repo = Repository(session)
        try:
            row = await repo.create_evaluator_provider(
                name=req.name,
                provider_type=req.provider_type,
                base_url=req.base_url,
                api_key_encrypted=encrypted,
                default_model=req.default_model,
                extra_config=req.extra_config,
                is_active=req.is_active,
                created_by=admin.id if admin else None,
            )
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise HTTPException(status_code=400, detail=f"创建失败：{e}") from e
    return _row_to_response(row)


@router.get("/{provider_id}", response_model=EvaluatorProviderResponse)
async def get_provider(
    provider_id: str,
    _admin: UserRow = Depends(require_admin),
):
    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.get_evaluator_provider(uuid.UUID(provider_id))
    if row is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return _row_to_response(row)


@router.put("/{provider_id}", response_model=EvaluatorProviderResponse)
async def update_provider(
    provider_id: str,
    req: UpdateEvaluatorProviderRequest,
    _admin: UserRow = Depends(require_admin),
):
    if req.provider_type is not None:
        _validate_provider_type(req.provider_type)

    raw = req.model_dump(exclude_unset=True)
    api_key = raw.pop("api_key", "__OMITTED__")

    updates: dict[str, Any] = {k: v for k, v in raw.items() if v is not None}

    # api_key: omitted -> keep, "" -> clear, other -> re-encrypt
    if api_key != "__OMITTED__":
        if api_key == "" or api_key is None:
            updates["api_key_encrypted"] = None
        else:
            try:
                updates["api_key_encrypted"] = encrypt_secret(api_key)
            except CryptoUnavailable as e:
                raise HTTPException(status_code=503, detail=str(e)) from e

    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.update_evaluator_provider(uuid.UUID(provider_id), **updates)
        if row is None:
            raise HTTPException(status_code=404, detail="provider not found")
        await session.commit()
    return _row_to_response(row)


@router.delete("/{provider_id}")
async def delete_provider(
    provider_id: str,
    _admin: UserRow = Depends(require_admin),
):
    async with async_session_factory() as session:
        repo = Repository(session)
        ok = await repo.delete_evaluator_provider(uuid.UUID(provider_id))
        if not ok:
            raise HTTPException(status_code=404, detail="provider not found")
        await session.commit()
    return {"id": provider_id, "deleted": True}


# ───────────────────────────────────────────────────────────────────────
# Connectivity check
# ───────────────────────────────────────────────────────────────────────

def _models_endpoint(provider_type: str, base_url: str | None) -> str | None:
    """Pick the GET endpoint that lists available models for a sanity check.

    Returns None when we don't know how to test the provider yet — caller
    should fall back to a no-op success.
    """
    bu = (base_url or "").rstrip("/")
    if provider_type in ("openai", "openai_compatible", "deepseek", "custom"):
        if not bu:
            return None
        return f"{bu}/models"
    if provider_type == "anthropic":
        # Anthropic's /v1/models works with x-api-key auth and is cheap.
        return f"{bu or 'https://api.anthropic.com'}/v1/models"
    if provider_type == "azure":
        # Azure /openai/models?api-version=... needs api version; let the
        # caller wire up extra_config['api_version'] later. MVP skips test.
        return None
    return None


def _build_headers(provider_type: str, api_key: str | None) -> dict[str, str]:
    if not api_key:
        return {}
    if provider_type == "anthropic":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    if provider_type == "azure":
        return {"api-key": api_key}
    # openai / openai_compatible / deepseek / custom -> Bearer
    return {"Authorization": f"Bearer {api_key}"}


async def _fetch_models(
    provider_type: str,
    base_url: str | None,
    api_key: str | None,
    *,
    timeout: float = 15.0,
) -> tuple[bool, int | None, str, list[str]]:
    """Hit the provider's ``/models`` endpoint.

    Returns ``(ok, latency_ms, detail, models)``. ``ok=False`` covers both
    "no /models check defined" and HTTP/network failures so callers don't
    need to inspect the detail string. The api key is never echoed back
    in ``detail`` — at worst the body preview from a 4xx response surfaces
    the URL path the provider used.
    """
    # Agent (SSE) providers have no /models listing and judging can take
    # >30s per call — a short ping here would false-negative. The real
    # connectivity check is the evaluator dry-run path.
    if provider_type == "agent":
        return (
            True, None,
            "Agent (SSE) 端点：请用评估器 dry-run 验证连通性",
            [],
        )
    url = _models_endpoint(provider_type, base_url)
    if url is None:
        return (
            False, None,
            f"no /models check defined for provider_type={provider_type}",
            [],
        )
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=_build_headers(provider_type, api_key))
    except Exception as e:
        return (False, None, f"connection error: {type(e).__name__}: {e}", [])
    latency = int((time.monotonic() - started) * 1000)

    if resp.status_code >= 400:
        body_preview = resp.text[:200].replace("\n", " ")
        return (False, latency, f"HTTP {resp.status_code}: {body_preview}", [])

    models: list[str] = []
    try:
        body = resp.json()
        items = body.get("data") if isinstance(body, dict) else None
        if isinstance(items, list):
            for it in items[:50]:
                if isinstance(it, dict) and isinstance(it.get("id"), str):
                    models.append(it["id"])
    except Exception:
        pass
    return (True, latency, f"OK · {len(models)} models" if models else "OK", models)


@router.post("/{provider_id}/test", response_model=TestProviderResponse)
async def test_provider(
    provider_id: str,
    _admin: UserRow = Depends(require_admin),
):
    """Sanity-check a provider by calling its `/models` endpoint.

    Returns ``ok=true`` plus a short model id sample on success. On HTTP
    error, surfaces the status + first 200 chars of the body so the editor
    UI can show a useful message without leaking the API key.
    """
    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.get_evaluator_provider(uuid.UUID(provider_id))
    if row is None:
        raise HTTPException(status_code=404, detail="provider not found")

    api_key = decrypt_secret(row.api_key_encrypted) if row.api_key_encrypted else None
    ok, latency, detail, models = await _fetch_models(
        row.provider_type, row.base_url, api_key,
    )
    return TestProviderResponse(
        ok=ok, latency_ms=latency, detail=detail, models=models,
    )


@router.get("/{provider_id}/models", response_model=ProviderModelsResponse)
async def list_provider_models(
    provider_id: str,
    _admin: UserRow = Depends(require_admin),
):
    """Return the provider's available model ids for the editor dropdown.

    Same call as ``/test`` but trimmed: no latency, no auth-failure UX
    distinction — the editor just wants a list (or an empty list with a
    short hint when the provider doesn't expose ``/models``).
    """
    async with async_session_factory() as session:
        repo = Repository(session)
        row = await repo.get_evaluator_provider(uuid.UUID(provider_id))
    if row is None:
        raise HTTPException(status_code=404, detail="provider not found")

    api_key = decrypt_secret(row.api_key_encrypted) if row.api_key_encrypted else None
    ok, _, detail, models = await _fetch_models(
        row.provider_type, row.base_url, api_key,
    )
    return ProviderModelsResponse(ok=ok, models=models, detail=detail)
