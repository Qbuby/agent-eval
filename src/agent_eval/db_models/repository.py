from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db_models.tables import (
    DatasetVersionRow,
    EvaluationScoreRow,
    LoopControlLogRow,
    OptimizationRow,
    TestCaseRow,
    TestResultRow,
    TestRunRow,
)


class Repository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ---- Dataset Versions ----

    async def create_dataset_version(
        self, version_tag: str, description: str = "", parent_version: str | None = None
    ) -> DatasetVersionRow:
        row = DatasetVersionRow(
            version_tag=version_tag, description=description, parent_version=parent_version
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_dataset_version(self, version_tag: str) -> DatasetVersionRow | None:
        stmt = select(DatasetVersionRow).where(DatasetVersionRow.version_tag == version_tag)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ---- Test Cases ----

    async def create_test_case(self, dataset_version_id: uuid.UUID, **kwargs: Any) -> TestCaseRow:
        row = TestCaseRow(dataset_version_id=dataset_version_id, **kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_cases_by_version(self, dataset_version_id: uuid.UUID) -> list[TestCaseRow]:
        stmt = select(TestCaseRow).where(TestCaseRow.dataset_version_id == dataset_version_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ---- Test Runs ----

    async def create_test_run(
        self,
        dataset_version_id: uuid.UUID,
        agent_config: dict,
        optimization_id: uuid.UUID | None = None,
        ab_group: str | None = None,
    ) -> TestRunRow:
        row = TestRunRow(
            dataset_version_id=dataset_version_id,
            agent_config=agent_config,
            optimization_id=optimization_id,
            ab_group=ab_group,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def finish_test_run(
        self, run_id: uuid.UUID, summary_scores: dict, status: str = "completed"
    ) -> None:
        stmt = select(TestRunRow).where(TestRunRow.id == run_id)
        result = await self.session.execute(stmt)
        row = result.scalar_one()
        row.status = status
        row.finished_at = datetime.now(timezone.utc)
        row.summary_scores = summary_scores

    # ---- Test Results ----

    async def create_test_result(self, run_id: uuid.UUID, test_case_id: uuid.UUID, **kwargs: Any) -> TestResultRow:
        row = TestResultRow(run_id=run_id, test_case_id=test_case_id, **kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_results_by_run(self, run_id: uuid.UUID) -> list[TestResultRow]:
        stmt = select(TestResultRow).where(TestResultRow.run_id == run_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ---- Evaluation Scores ----

    async def create_eval_score(self, result_id: uuid.UUID, **kwargs: Any) -> EvaluationScoreRow:
        row = EvaluationScoreRow(result_id=result_id, **kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_scores_by_run(self, run_id: uuid.UUID) -> dict[uuid.UUID, list[EvaluationScoreRow]]:
        stmt = (
            select(EvaluationScoreRow)
            .join(TestResultRow, EvaluationScoreRow.result_id == TestResultRow.id)
            .where(TestResultRow.run_id == run_id)
        )
        result = await self.session.execute(stmt)
        scores: dict[uuid.UUID, list[EvaluationScoreRow]] = {}
        for row in result.scalars().all():
            scores.setdefault(row.result_id, []).append(row)
        return scores

    # ---- Optimizations ----

    async def create_optimization(self, source_run_id: uuid.UUID, iteration: int, **kwargs: Any) -> OptimizationRow:
        row = OptimizationRow(source_run_id=source_run_id, iteration=iteration, **kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_optimizations_by_session(self, source_run_id: uuid.UUID) -> list[OptimizationRow]:
        stmt = select(OptimizationRow).where(OptimizationRow.source_run_id == source_run_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ---- Loop Control Log ----

    async def log_loop_iteration(
        self,
        loop_session_id: uuid.UUID,
        iteration: int,
        run_id: uuid.UUID | None = None,
        optimization_id: uuid.UUID | None = None,
        aggregate_score: float | None = None,
        target_score: float | None = None,
        converged: bool = False,
        safety_stopped: bool = False,
        reason: str | None = None,
    ) -> LoopControlLogRow:
        row = LoopControlLogRow(
            loop_session_id=loop_session_id,
            iteration=iteration,
            run_id=run_id,
            optimization_id=optimization_id,
            aggregate_score=aggregate_score,
            target_score=target_score,
            converged=converged,
            safety_stopped=safety_stopped,
            reason=reason,
        )
        self.session.add(row)
        await self.session.flush()
        return row
