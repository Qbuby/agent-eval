from __future__ import annotations

from typing import Any

from agent_eval.config_service import config_service
from agent_eval.data.case_generator import CaseGenerator
from agent_eval.data.dataset_manager import DatasetManager
from agent_eval.data.langfuse_provider import (
    LangfuseDatasetProvider,
    build_langfuse_client,
)
from agent_eval.data.langsmith_provider import LangSmithDatasetProvider
from agent_eval.data.trace_extractor import TraceExtractor


async def _get_langsmith_kwargs() -> dict[str, Any]:
    # Resolve the active LangSmith connection preset (langsmith.connection),
    # which itself falls back to the legacy single keys + env settings.
    conn = await config_service.get_langsmith_connection()
    kwargs: dict[str, Any] = {}
    if conn.get("api_key"):
        kwargs["api_key"] = conn["api_key"]
    if conn.get("api_url"):
        kwargs["api_url"] = conn["api_url"]
    return kwargs


async def get_provider() -> LangfuseDatasetProvider:
    # Dataset storage now lives in the self-hosted Langfuse instance (the
    # LangSmith cloud creds are dead — see migration). The LangSmith provider
    # is kept only for the external-import paths (get_langsmith_manager).
    client = await build_langfuse_client()
    return LangfuseDatasetProvider(client)


async def get_manager() -> DatasetManager:
    return DatasetManager(provider=await get_provider())


async def get_langsmith_provider() -> LangSmithDatasetProvider:
    # LangSmith-backed provider — ONLY for external-dataset import features
    # (pull_external_dataset / import-langsmith). Default storage is Langfuse.
    kwargs = await _get_langsmith_kwargs()
    return LangSmithDatasetProvider(**kwargs)


async def get_langsmith_manager() -> DatasetManager:
    return DatasetManager(provider=await get_langsmith_provider())


async def get_extractor() -> TraceExtractor:
    kwargs = await _get_langsmith_kwargs()
    return TraceExtractor(**kwargs)


async def resolve_target_agent_cfg(endpoint_url: str | None = None) -> dict[str, Any]:
    """Resolve the *agent under test* config dict from the saved ``target_agent.*``
    settings. ``endpoint_url`` overrides the saved ``target_agent.endpoint_url``
    default (the sample-generation UI passes one of the pre-configured endpoint
    presets); api_key / timeout / type still come from the shared config.

    Returns the ``agent_cfg`` dict consumed by ``_make_adapter``. Raises 400 if no
    endpoint is configured. Kept separate from adapter construction so callers
    that need to build a *per-case* adapter (e.g. multi-turn replay needs a fixed
    thread_id) can reuse the resolved config."""
    from fastapi import HTTPException

    resolved_url = endpoint_url or await config_service.get("target_agent.endpoint_url")
    if not resolved_url:
        raise HTTPException(
            status_code=400,
            detail="未配置测试目标 agent 端点（在 配置 → target_agent.endpoint_url 中设置），"
            "样例生成需要连接被测 agent。",
        )

    api_key = await config_service.get("target_agent.api_key") or ""
    raw_timeout = await config_service.get("target_agent.timeout")
    try:
        timeout = float(raw_timeout) if raw_timeout else 120.0
    except (TypeError, ValueError):
        timeout = 120.0
    # The production agent speaks the LangGraph SSE protocol; allow an
    # optional target_agent.type override (key may be absent → defaults sse).
    agent_type = (await config_service.get("target_agent.type")) or "sse"

    return {
        "type": agent_type,
        "url": resolved_url,
        "api_key": api_key,
        "timeout": timeout,
    }


async def build_generator_adapter(endpoint_url: str | None = None):
    """Build the *agent under test* HTTP adapter from the resolved config.

    Cases are authored by the agent from its own knowledge graph, so this
    deliberately does NOT use a bare LLM. When omitted the saved default endpoint
    is used (back-compat). Returns the built adapter; the caller owns closing it
    (``await adapter.close()``)."""
    from agent_eval.evaluation.langfuse_runner import _make_adapter

    agent_cfg = await resolve_target_agent_cfg(endpoint_url)
    return _make_adapter(agent_cfg)


async def get_generator():
    """FastAPI cleanup dependency: yield a CaseGenerator on the saved default
    endpoint and close the adapter afterwards. Endpoints that need to target a
    caller-chosen endpoint build the generator directly via
    ``build_generator_adapter`` instead."""
    adapter = await build_generator_adapter()
    try:
        yield CaseGenerator(adapter=adapter)
    finally:
        try:
            await adapter.close()
        except Exception:
            pass


async def get_routing_engine():
    from agent_eval.routing.engine import RoutingEngine

    return RoutingEngine(
        extractor=await get_extractor(),
        provider=await get_provider(),
    )
