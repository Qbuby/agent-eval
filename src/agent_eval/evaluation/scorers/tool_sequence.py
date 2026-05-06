from __future__ import annotations

from agent_eval.evaluation.scorers.base import DimensionScorer
from agent_eval.models.score import DimensionScore
from agent_eval.models.test_case import TestCase, ToolCallExpectation
from agent_eval.models.trace import AgentTrace, ToolCallRecord


class ToolSequenceScorer(DimensionScorer):
    dimension = "tool_sequence_correctness"

    async def score(self, test_case: TestCase, trace: AgentTrace) -> DimensionScore:
        expected = test_case.expected_tool_calls
        actual = trace.tool_calls

        if not expected:
            penalty = self._redundancy_score(actual, max_calls=test_case.max_tool_calls)
            return DimensionScore(
                dimension=self.dimension,
                score=penalty,
                scoring_method="rule",
                details={"note": "no expected sequence, scored on call count only"},
            )

        coverage = self._coverage_score(expected, actual)
        order = self._order_score(expected, actual)
        params = self._param_score(expected, actual)
        redundancy = self._redundancy_score(actual, max_calls=test_case.max_tool_calls)

        final = coverage * 0.35 + order * 0.25 + params * 0.25 + redundancy * 0.15

        return DimensionScore(
            dimension=self.dimension,
            score=round(final, 4),
            scoring_method="rule",
            details={
                "coverage": coverage,
                "order": order,
                "params": params,
                "redundancy": redundancy,
            },
        )

    def _coverage_score(
        self, expected: list[ToolCallExpectation], actual: list[ToolCallRecord]
    ) -> float:
        required = [e for e in expected if e.required]
        if not required:
            return 1.0

        actual_names = [a.tool_name for a in actual]
        found = sum(1 for r in required if r.tool_name in actual_names)
        return found / len(required)

    def _order_score(
        self, expected: list[ToolCallExpectation], actual: list[ToolCallRecord]
    ) -> float:
        if not expected or not actual:
            return 1.0 if not expected else 0.0

        expected_names = [e.tool_name for e in sorted(expected, key=lambda x: x.order)]
        actual_names = [a.tool_name for a in actual]

        lcs_len = self._lcs_length(expected_names, actual_names)
        return lcs_len / len(expected_names) if expected_names else 1.0

    def _param_score(
        self, expected: list[ToolCallExpectation], actual: list[ToolCallRecord]
    ) -> float:
        matchable = [(e, a) for e in expected if e.args_matcher for a in actual if a.tool_name == e.tool_name]
        if not matchable:
            return 1.0

        total, matched = 0, 0
        for exp, act in matchable:
            for key, val in exp.args_matcher.items():
                total += 1
                if act.tool_input.get(key) == val:
                    matched += 1

        return matched / total if total else 1.0

    def _redundancy_score(self, actual: list[ToolCallRecord], max_calls: int | None) -> float:
        if max_calls is None:
            return 1.0
        if not actual:
            return 1.0
        if len(actual) <= max_calls:
            return 1.0
        return max(0.0, 1.0 - (len(actual) - max_calls) / max_calls)

    @staticmethod
    def _lcs_length(a: list[str], b: list[str]) -> int:
        m, n = len(a), len(b)
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if a[i - 1] == b[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1] + 1
                else:
                    dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
        return dp[m][n]
