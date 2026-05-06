from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from agent_eval.evaluation.scorers.base import DimensionScorer
from agent_eval.evaluation.trace_collector import TraceCollectorCallback
from agent_eval.models.score import CaseResult, DimensionScore, RunSummary
from agent_eval.models.test_case import TestCase
from agent_eval.models.trace import AgentTrace

logger = logging.getLogger(__name__)


class AgentProtocol(Protocol):
    async def ainvoke(self, input: dict[str, Any], config: dict[str, Any] | None = None) -> Any:
        ...


class EvaluationOrchestrator:
    def __init__(self, scorers: list[DimensionScorer], concurrency: int = 5):
        self.scorers = {s.dimension: s for s in scorers}
        self.concurrency = concurrency

    async def evaluate_single(
        self, agent: AgentProtocol, test_case: TestCase
    ) -> tuple[AgentTrace, CaseResult]:
        callback = TraceCollectorCallback()
        try:
            result = await agent.ainvoke(
                {"messages": test_case.input_messages},
                config={"callbacks": [callback]},
            )
            if isinstance(result, dict) and "output" in result:
                callback.trace.final_output = result["output"]
            elif isinstance(result, str):
                callback.trace.final_output = result
        except Exception as e:
            callback.trace.error = str(e)
            logger.warning("Agent execution failed for case %s: %s", test_case.name, e)

        trace = callback.trace
        trace.total_latency_ms = sum(s.latency_ms for s in trace.reasoning_steps)

        dimension_scores: dict[str, DimensionScore] = {}
        for dim, scorer in self.scorers.items():
            try:
                ds = await scorer.score(test_case, trace)
                weight = getattr(test_case.eval_weights, dim, 0.0)
                ds.weight = weight
                ds.weighted_score = round(ds.score * weight, 4)
                dimension_scores[dim] = ds
            except Exception as e:
                logger.error("Scorer %s failed for case %s: %s", dim, test_case.name, e)
                dimension_scores[dim] = DimensionScore(
                    dimension=dim, score=0.0, scoring_method="error",
                    details={"error": str(e)},
                )

        aggregate = sum(ds.weighted_score for ds in dimension_scores.values())

        case_result = CaseResult(
            test_case_id=test_case.id,
            aggregate_score=round(aggregate, 4),
            dimension_scores=dimension_scores,
            passed=aggregate >= 0.7,
        )

        return trace, case_result

    async def evaluate_batch(
        self, agent: AgentProtocol, test_cases: list[TestCase], run_id: str | None = None
    ) -> RunSummary:
        run_id = run_id or str(uuid.uuid4())
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _eval_with_limit(tc: TestCase) -> tuple[AgentTrace, CaseResult]:
            async with semaphore:
                return await self.evaluate_single(agent, tc)

        tasks = [_eval_with_limit(tc) for tc in test_cases]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        case_results: list[CaseResult] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error("Evaluation failed for case %s: %s", test_cases[i].name, r)
                case_results.append(CaseResult(
                    test_case_id=test_cases[i].id, aggregate_score=0.0, passed=False,
                ))
            else:
                _, cr = r
                case_results.append(cr)

        passed = sum(1 for cr in case_results if cr.passed)
        total = len(case_results)
        agg = sum(cr.aggregate_score for cr in case_results) / total if total else 0.0

        dim_totals: dict[str, list[float]] = {}
        for cr in case_results:
            for dim, ds in cr.dimension_scores.items():
                dim_totals.setdefault(dim, []).append(ds.score)
        dim_avgs = {dim: sum(vals) / len(vals) for dim, vals in dim_totals.items()}

        return RunSummary(
            run_id=run_id,
            total_cases=total,
            passed_cases=passed,
            failed_cases=total - passed,
            aggregate_score=round(agg, 4),
            dimension_averages=dim_avgs,
            case_results=case_results,
        )
