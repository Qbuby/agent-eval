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


class TurnExpectation(BaseModel):
    """多轮对话中针对单个 user→assistant 轮的期望（评估期使用）。

    turn_index 指向 input_messages 里该 user 消息的下标（0-based）。
    criteria / expected_output 均可选——不强制每轮都填，只标注关注的轮次。
    """

    turn_index: int
    criteria: list[str] = []
    expected_output: str | None = None


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
    source: Literal["manual", "auto_generated", "failure_derived", "trace_derived", "external", "file_imported"] = "manual"

    input_messages: list[dict[str, Any]]
    agent_config_override: dict[str, Any] | None = None

    # 多轮对话场景：对话级总目标 + 按轮期望。单轮 case 这两个字段为空，
    # 与历史数据完全兼容。turn_expectations 的 turn_index 指向 input_messages
    # 里 user 消息的下标。
    conversation_goal: str | None = None
    turn_expectations: list[TurnExpectation] = []

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
