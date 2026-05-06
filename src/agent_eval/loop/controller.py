from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from agent_eval.evaluation.orchestrator import EvaluationOrchestrator
from agent_eval.evaluation.regression import RegressionDetector
from agent_eval.models.optimization import LoopResult, OptimizationStrategy
from agent_eval.models.score import RunSummary
from agent_eval.models.test_case import TestCase
from agent_eval.optimization.failure_analyzer import FailureAnalyzer
from agent_eval.optimization.strategy_applicator import StrategyApplicator
from agent_eval.optimization.strategy_generator import StrategyGenerator

logger = logging.getLogger(__name__)


class AgentFactory(Protocol):
    def get_config(self) -> dict[str, Any]: ...
    def create(self, config: dict[str, Any]) -> Any: ...


@dataclass
class LoopConfig:
    target_score: float = 0.85
    max_iterations: int = 10
    min_improvement: float = 0.01
    stagnation_patience: int = 3
    regression_tolerance: float = 0.05
    enable_ab_test: bool = True
    ab_test_ratio: float = 0.3


class LoopController:
    def __init__(
        self,
        config: LoopConfig,
        evaluator: EvaluationOrchestrator,
        analyzer: FailureAnalyzer,
        generator: StrategyGenerator,
        applicator: StrategyApplicator,
    ):
        self.config = config
        self.evaluator = evaluator
        self.analyzer = analyzer
        self.generator = generator
        self.applicator = applicator
        self.regression_detector = RegressionDetector(threshold=config.regression_tolerance)

    async def run_loop(
        self,
        agent_factory: AgentFactory,
        test_cases: list[TestCase],
        on_iteration: Any = None,
    ) -> LoopResult:
        session_id = str(uuid.uuid4())
        current_config = agent_factory.get_config()
        best_score = 0.0
        best_config = current_config.copy()
        stagnation_count = 0
        history: list[OptimizationStrategy] = []
        baseline_results: dict[str, dict[str, float]] | None = None

        logger.info("Starting optimization loop (session=%s, target=%.2f, max_iter=%d)",
                     session_id, self.config.target_score, self.config.max_iterations)

        for iteration in range(1, self.config.max_iterations + 1):
            logger.info("=== Iteration %d/%d ===", iteration, self.config.max_iterations)

            # 1. Run evaluation
            agent = agent_factory.create(current_config)
            run_summary = await self.evaluator.evaluate_batch(agent, test_cases)
            aggregate_score = run_summary.aggregate_score

            logger.info("Score: %.4f (target: %.4f, best: %.4f)",
                        aggregate_score, self.config.target_score, best_score)

            if on_iteration:
                await on_iteration(iteration, run_summary, current_config)

            # 2. Convergence check
            if aggregate_score >= self.config.target_score:
                logger.info("Target reached at iteration %d", iteration)
                return LoopResult(
                    converged=True, iterations=iteration,
                    final_score=aggregate_score, best_score=aggregate_score,
                    final_config=current_config, optimization_history=history,
                    stop_reason="target_reached",
                )

            # 3. Regression check (from iteration 2 onward)
            current_results = self._extract_dim_scores(run_summary)
            if baseline_results is not None:
                report = self.regression_detector.detect(baseline_results, current_results)
                if report.has_regression:
                    worst = report.worst_regression
                    logger.warning(
                        "Regression detected: %s in %s (delta=%.4f). Rolling back.",
                        worst.dimension, worst.test_case_id[:8], worst.delta,
                    )
                    current_config = best_config.copy()
                    stagnation_count += 1

                    if stagnation_count >= self.config.stagnation_patience:
                        return LoopResult(
                            converged=False, iterations=iteration,
                            final_score=best_score, best_score=best_score,
                            final_config=best_config, optimization_history=history,
                            stop_reason="regression_with_stagnation",
                        )
                    continue

            # 4. Stagnation check
            improvement = aggregate_score - best_score
            if improvement < self.config.min_improvement:
                stagnation_count += 1
                logger.info("Stagnation %d/%d (improvement=%.4f)",
                            stagnation_count, self.config.stagnation_patience, improvement)
                if stagnation_count >= self.config.stagnation_patience:
                    return LoopResult(
                        converged=False, iterations=iteration,
                        final_score=aggregate_score, best_score=best_score,
                        final_config=best_config, optimization_history=history,
                        stop_reason="stagnation",
                    )
            else:
                stagnation_count = 0
                best_score = aggregate_score
                best_config = current_config.copy()
                baseline_results = current_results

            # 5. Failure analysis
            traces = {}  # In production, collect from evaluate_batch
            case_infos = {tc.id: tc.model_dump() for tc in test_cases}
            clusters = await self.analyzer.analyze(run_summary, traces, case_infos)

            if not clusters:
                logger.info("No failure clusters found, stopping")
                return LoopResult(
                    converged=False, iterations=iteration,
                    final_score=aggregate_score, best_score=best_score,
                    final_config=current_config, optimization_history=history,
                    stop_reason="no_failures_to_optimize",
                )

            # 6. Generate and apply strategy
            strategy = await self.generator.generate(clusters, current_config, history)
            history.append(strategy)

            if not strategy.changes:
                logger.warning("Strategy generated no changes, stopping")
                return LoopResult(
                    converged=False, iterations=iteration,
                    final_score=aggregate_score, best_score=best_score,
                    final_config=current_config, optimization_history=history,
                    stop_reason="no_strategy_changes",
                )

            logger.info("Applying strategy: %s (%d changes, risk=%s)",
                        strategy.strategy_type, len(strategy.changes), strategy.risk_level)

            current_config = self.applicator.apply(current_config, strategy)

        return LoopResult(
            converged=False, iterations=self.config.max_iterations,
            final_score=best_score, best_score=best_score,
            final_config=best_config, optimization_history=history,
            stop_reason="max_iterations_reached",
        )

    @staticmethod
    def _extract_dim_scores(summary: RunSummary) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {}
        for cr in summary.case_results:
            result[cr.test_case_id] = {
                dim: ds.score for dim, ds in cr.dimension_scores.items()
            }
        return result
