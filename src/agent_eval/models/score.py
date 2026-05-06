from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DimensionScore:
    dimension: str
    score: float
    weight: float = 0.0
    weighted_score: float = 0.0
    scoring_method: str = "rule"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseResult:
    test_case_id: str
    aggregate_score: float
    dimension_scores: dict[str, DimensionScore] = field(default_factory=dict)
    passed: bool = False


@dataclass
class RunSummary:
    run_id: str
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    aggregate_score: float = 0.0
    dimension_averages: dict[str, float] = field(default_factory=dict)
    case_results: list[CaseResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.total_cases == 0:
            return 0.0
        return self.passed_cases / self.total_cases
