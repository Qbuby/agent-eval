from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCallExpectation(BaseModel):
    tool_name: str
    args_matcher: dict[str, Any] | None = None
    order: int
    required: bool = True
    allow_retry: bool = False


class EvalWeights(BaseModel):
    output_correctness: float = 0.30
    tool_sequence_correctness: float = 0.25
    reasoning_quality: float = 0.20
    performance: float = 0.15
    error_recovery: float = 0.10


class TestCase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    dataset_version: str
    name: str
    description: str = ""
    tags: list[str] = []
    source: Literal["manual", "auto_generated", "failure_derived", "trace_derived", "external"] = "manual"

    input_messages: list[dict[str, str]]
    agent_config_override: dict[str, Any] | None = None

    expected_output: str | None = None
    expected_output_criteria: list[str] = []
    expected_tool_calls: list[ToolCallExpectation] = []
    max_tool_calls: int | None = None
    max_latency_ms: int | None = None
    max_tokens: int | None = None

    eval_weights: EvalWeights = Field(default_factory=EvalWeights)
    scoring_mode: Literal["rule", "llm", "hybrid"] = "hybrid"

    parent_case_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
