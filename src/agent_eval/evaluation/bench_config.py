from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    type: str = Field(default="openai", description="Agent type: openai | sse")
    url: str = Field(default="", description="Agent API endpoint URL")
    api_key: str = Field(default="", description="API key (for OpenAI-compatible)")
    model: str = Field(default="default", description="Model name (for OpenAI-compatible)")
    headers: dict[str, str] = Field(default_factory=dict, description="Extra HTTP headers")
    payload_template: dict[str, Any] = Field(default_factory=dict, description="Payload template for SSE")
    timeout: float = Field(default=120, description="Request timeout in seconds")


class JudgeDimensionConfig(BaseModel):
    name: str
    weight: float = 1.0
    description: str = ""


class JudgeConfig(BaseModel):
    model: str = Field(default="", description="Judge LLM model name")
    base_url: str = Field(default="", description="Judge LLM base URL")
    api_key: str = Field(default="", description="Judge LLM API key")
    temperature: float = 0.0
    dimensions: list[JudgeDimensionConfig] = Field(default_factory=lambda: [
        JudgeDimensionConfig(name="准确性", weight=0.4, description="回答是否准确、事实正确"),
        JudgeDimensionConfig(name="完整性", weight=0.3, description="回答是否完整覆盖问题要点"),
        JudgeDimensionConfig(name="相关性", weight=0.3, description="回答是否与问题相关、不跑题"),
    ])


class BenchConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    dataset: str = Field(default="", description="Dataset name to evaluate")
    split: str | None = None
    tags: list[str] | None = None
    concurrency: int = Field(default=3, ge=1, le=20)
    output_dir: str = Field(default="./eval_results")
    limit: int | None = Field(default=None, description="Max cases to evaluate")

    @classmethod
    def from_yaml(cls, path: str | Path) -> BenchConfig:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchConfig:
        return cls(**data)
