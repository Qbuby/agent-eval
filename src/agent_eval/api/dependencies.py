from __future__ import annotations

from typing import Any

from agent_eval.config import settings
from agent_eval.config_service import config_service
from agent_eval.data.case_generator import CaseGenerator
from agent_eval.data.dataset_manager import DatasetManager
from agent_eval.data.langsmith_provider import LangSmithDatasetProvider
from agent_eval.data.trace_extractor import TraceExtractor


async def _get_langsmith_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    api_key = await config_service.get("langsmith.api_key")
    if not api_key and settings.langsmith.api_key:
        api_key = settings.langsmith.api_key
    if api_key:
        kwargs["api_key"] = api_key

    api_url = await config_service.get("langsmith.api_url")
    if not api_url and settings.langsmith.api_url:
        api_url = settings.langsmith.api_url
    if api_url:
        kwargs["api_url"] = api_url
    return kwargs


async def get_provider() -> LangSmithDatasetProvider:
    kwargs = await _get_langsmith_kwargs()
    return LangSmithDatasetProvider(**kwargs)


async def get_manager() -> DatasetManager:
    return DatasetManager(provider=await get_provider())


async def get_extractor() -> TraceExtractor:
    kwargs = await _get_langsmith_kwargs()
    return TraceExtractor(**kwargs)


async def get_generator() -> CaseGenerator:
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": settings.llm.model,
        "temperature": settings.llm.temperature,
        "max_tokens": settings.llm.max_tokens,
    }

    api_key = await config_service.get("llm.api_key")
    if not api_key and settings.llm.api_key:
        api_key = settings.llm.api_key
    if api_key:
        kwargs["api_key"] = api_key

    base_url = await config_service.get("llm.base_url")
    if not base_url and settings.llm.base_url:
        base_url = settings.llm.base_url
    if base_url:
        kwargs["base_url"] = base_url

    return CaseGenerator(llm=ChatOpenAI(**kwargs))


async def get_routing_engine():
    from agent_eval.routing.engine import RoutingEngine

    return RoutingEngine(
        extractor=await get_extractor(),
        provider=await get_provider(),
    )
