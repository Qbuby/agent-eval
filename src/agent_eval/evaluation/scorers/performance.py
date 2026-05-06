from __future__ import annotations

from agent_eval.evaluation.scorers.base import DimensionScorer
from agent_eval.models.score import DimensionScore
from agent_eval.models.test_case import TestCase
from agent_eval.models.trace import AgentTrace


class PerformanceScorer(DimensionScorer):
    dimension = "performance"

    async def score(self, test_case: TestCase, trace: AgentTrace) -> DimensionScore:
        sub_scores: dict[str, float] = {}

        if test_case.max_latency_ms and trace.total_latency_ms > 0:
            sub_scores["latency"] = min(1.0, test_case.max_latency_ms / trace.total_latency_ms)

        if test_case.max_tokens and trace.total_tokens > 0:
            sub_scores["tokens"] = min(1.0, test_case.max_tokens / trace.total_tokens)

        if test_case.max_tool_calls and trace.tool_calls:
            actual_count = len(trace.tool_calls)
            sub_scores["tool_calls"] = min(1.0, test_case.max_tool_calls / actual_count)

        if not sub_scores:
            return DimensionScore(
                dimension=self.dimension, score=1.0, scoring_method="rule",
                details={"note": "no performance thresholds defined"},
            )

        final = sum(sub_scores.values()) / len(sub_scores)
        return DimensionScore(
            dimension=self.dimension,
            score=round(final, 4),
            scoring_method="rule",
            details=sub_scores,
        )
