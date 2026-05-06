from __future__ import annotations

from functools import lru_cache
from typing import Any

from agent_eval.config import settings
from agent_eval.data.case_generator import CaseGenerator
from agent_eval.data.dataset_manager import DatasetManager
from agent_eval.data.langsmith_provider import LangSmithDatasetProvider
from agent_eval.data.trace_extractor import TraceExtractor


@lru_cache
def get_provider() -> LangSmithDatasetProvider:
    kwargs: dict[str, Any] = {}
    if settings.langsmith.api_key:
        kwargs["api_key"] = settings.langsmith.api_key
    if settings.langsmith.api_url:
        kwargs["api_url"] = settings.langsmith.api_url
    return LangSmithDatasetProvider(**kwargs)


@lru_cache
def get_manager() -> DatasetManager:
    return DatasetManager(provider=get_provider())


@lru_cache
def get_extractor() -> TraceExtractor:
    kwargs: dict[str, Any] = {}
    if settings.langsmith.api_key:
        kwargs["api_key"] = settings.langsmith.api_key
    if settings.langsmith.api_url:
        kwargs["api_url"] = settings.langsmith.api_url
    return TraceExtractor(**kwargs)


@lru_cache
def get_generator() -> CaseGenerator:
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": settings.llm.model,
        "temperature": settings.llm.temperature,
        "max_tokens": settings.llm.max_tokens,
    }
    if settings.llm.api_key:
        kwargs["api_key"] = settings.llm.api_key
    if settings.llm.base_url:
        kwargs["base_url"] = settings.llm.base_url
    return CaseGenerator(llm=ChatOpenAI(**kwargs))
