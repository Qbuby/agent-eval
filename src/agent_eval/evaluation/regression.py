from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RegressionItem:
    test_case_id: str
    dimension: str
    baseline_score: float
    current_score: float
    delta: float


@dataclass
class RegressionReport:
    baseline_run_id: str
    current_run_id: str
    regressions: list[RegressionItem] = field(default_factory=list)

    @property
    def has_regression(self) -> bool:
        return len(self.regressions) > 0

    @property
    def worst_regression(self) -> RegressionItem | None:
        if not self.regressions:
            return None
        return min(self.regressions, key=lambda r: r.delta)


class RegressionDetector:
    def __init__(self, threshold: float = 0.05):
        self.threshold = threshold

    def detect(
        self,
        baseline_results: dict[str, dict[str, float]],
        current_results: dict[str, dict[str, float]],
        baseline_run_id: str = "",
        current_run_id: str = "",
    ) -> RegressionReport:
        regressions: list[RegressionItem] = []

        for case_id, baseline_dims in baseline_results.items():
            current_dims = current_results.get(case_id)
            if current_dims is None:
                continue

            for dim, baseline_score in baseline_dims.items():
                current_score = current_dims.get(dim)
                if current_score is None:
                    continue

                delta = current_score - baseline_score
                if delta < -self.threshold:
                    regressions.append(RegressionItem(
                        test_case_id=case_id,
                        dimension=dim,
                        baseline_score=baseline_score,
                        current_score=current_score,
                        delta=delta,
                    ))

        return RegressionReport(
            baseline_run_id=baseline_run_id,
            current_run_id=current_run_id,
            regressions=regressions,
        )
