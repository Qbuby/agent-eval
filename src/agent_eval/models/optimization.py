from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class FailureLabel:
    category: str
    confidence: float
    explanation: str


@dataclass
class FailureCluster:
    category: str
    count: int
    test_case_ids: list[str] = field(default_factory=list)
    summary: str = ""
    suggested_fix_direction: str = ""
    sample_errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StrategyChange:
    change_type: Literal[
        "prompt_modification",
        "tool_description",
        "tool_parameter",
        "system_parameter",
    ]
    target: str
    before: str | None = None
    after: str = ""
    reason: str = ""


@dataclass
class OptimizationStrategy:
    strategy_type: Literal["prompt", "tool_config", "system_param", "composite"]
    changes: list[StrategyChange] = field(default_factory=list)
    rationale: str = ""
    expected_improvement: dict[str, float] = field(default_factory=dict)
    risk_level: Literal["low", "medium", "high"] = "low"


@dataclass
class LoopResult:
    converged: bool
    iterations: int
    final_score: float
    best_score: float = 0.0
    final_config: dict[str, Any] = field(default_factory=dict)
    optimization_history: list[OptimizationStrategy] = field(default_factory=list)
    stop_reason: str = ""
