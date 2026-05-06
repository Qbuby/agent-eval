from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallRecord:
    tool_name: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_output: str | None = None
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str | None = None

    @property
    def duration_ms(self) -> float:
        return (self.finished_at - self.started_at) * 1000


@dataclass
class ReasoningStep:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    content: str = ""
    tool_calls_requested: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0


@dataclass
class AgentTrace:
    reasoning_steps: list[ReasoningStep] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_latency_ms: float = 0.0
    final_output: str = ""
    error: str | None = None

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)
