from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Float, Text, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.db_models.tables import (
    DatasetVersionRow,
    EvalCaseSourceRow,
    EvaluationScoreRow,
    EvaluatorConfigRow,
    LoopControlLogRow,
    OptimizationRow,
    RoutingLogRow,
    RoutingRuleRow,
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
        dataset_version_id: uuid.UUID | None = None,
        agent_config: dict | None = None,
        optimization_id: uuid.UUID | None = None,
        ab_group: str | None = None,
        benchmark_version_id: uuid.UUID | None = None,
        eval_case_source_id: uuid.UUID | None = None,
        langfuse_run_name: str | None = None,
        langsmith_project: str | None = None,
        evaluator_configs: list | None = None,
        status: str = "running",
        eval_started_at: datetime | None = None,
    ) -> TestRunRow:
        row = TestRunRow(
            dataset_version_id=dataset_version_id,
            benchmark_version_id=benchmark_version_id,
            eval_case_source_id=eval_case_source_id,
            agent_config=agent_config or {},
            optimization_id=optimization_id,
            ab_group=ab_group,
            langfuse_run_name=langfuse_run_name,
            langsmith_project=langsmith_project,
            evaluator_configs=evaluator_configs or [],
            status=status,
            started_at=datetime.now(timezone.utc),
            eval_started_at=eval_started_at or datetime.now(timezone.utc),
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

    async def get_test_run(self, run_id: uuid.UUID) -> TestRunRow | None:
        stmt = select(TestRunRow).where(TestRunRow.id == run_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_test_runs(
        self,
        *,
        benchmark_version_id: uuid.UUID | None = None,
        status: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        text_query: str | None = None,
        min_pass_rate: float | None = None,  # 0..1
        include_deleted: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TestRunRow], int]:
        """List test runs with the filters the history page exposes.

        text_query searches over run_name + agent_config.model + agent_config.url +
        langsmith_project (case-insensitive ILIKE).

        min_pass_rate is computed from summary_scores.counts.passed/total —
        runs missing those keys are excluded when this filter is set
        (pre-completion runs simply don't have a pass rate yet).
        """
        from sqlalchemy import func, or_, cast
        from sqlalchemy.dialects.postgresql import JSONB
        base = select(TestRunRow).order_by(TestRunRow.created_at.desc())
        count_stmt = select(func.count(TestRunRow.id))

        def _apply(q):
            if not include_deleted:
                q = q.where(TestRunRow.deleted_at.is_(None))
            if benchmark_version_id is not None:
                q = q.where(TestRunRow.benchmark_version_id == benchmark_version_id)
            if status is not None:
                q = q.where(TestRunRow.status == status)
            if started_after is not None:
                q = q.where(TestRunRow.started_at >= started_after)
            if started_before is not None:
                q = q.where(TestRunRow.started_at <= started_before)
            if text_query:
                pattern = f"%{text_query}%"
                # JSONB ->> returns text; cast a few fields and match.
                # We index agent_config.model / agent_config.url / langfuse_run_name /
                # langsmith_project, which is what users have to search by.
                q = q.where(or_(
                    TestRunRow.langfuse_run_name.ilike(pattern),
                    TestRunRow.langsmith_project.ilike(pattern),
                    cast(TestRunRow.agent_config["model"], Text).ilike(pattern),
                    cast(TestRunRow.agent_config["url"], Text).ilike(pattern),
                ))
            if min_pass_rate is not None:
                # summary_scores.counts.passed / counts.total >= min_pass_rate
                # In SQL we approximate with a check that requires both keys
                # to exist; rows missing them are filtered out.
                # Postgres syntax: cast jsonb numbers via ::float.
                q = q.where(
                    (TestRunRow.summary_scores["counts"]["total"].astext.cast(Float) > 0)
                    & (
                        (TestRunRow.summary_scores["counts"]["passed"].astext.cast(Float)
                         / TestRunRow.summary_scores["counts"]["total"].astext.cast(Float))
                        >= min_pass_rate
                    )
                )
            return q

        base = _apply(base)
        count_stmt = _apply(count_stmt)
        total = (await self.session.execute(count_stmt)).scalar_one()
        rows = (await self.session.execute(
            base.offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
        return list(rows), int(total)

    async def soft_delete_test_run(self, run_id: uuid.UUID) -> bool:
        """Mark a run as deleted (soft delete). Returns False if not found
        or already deleted; True on first delete."""
        from sqlalchemy import update
        result = await self.session.execute(
            update(TestRunRow)
            .where(TestRunRow.id == run_id)
            .where(TestRunRow.deleted_at.is_(None))
            .values(deleted_at=datetime.now(timezone.utc))
        )
        return result.rowcount > 0

    # ---- Test Results ----

    async def create_test_result(
        self,
        run_id: uuid.UUID,
        *,
        test_case_id: uuid.UUID | None = None,
        benchmark_case_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> TestResultRow:
        row = TestResultRow(
            run_id=run_id,
            test_case_id=test_case_id,
            benchmark_case_id=benchmark_case_id,
            **kwargs,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_results_by_run(self, run_id: uuid.UUID) -> list[TestResultRow]:
        stmt = select(TestResultRow).where(TestResultRow.run_id == run_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_results_paginated(
        self, run_id: uuid.UUID, page: int = 1, page_size: int = 50,
    ) -> tuple[list[TestResultRow], int]:
        from sqlalchemy import func
        count_stmt = select(func.count(TestResultRow.id)).where(TestResultRow.run_id == run_id)
        total = (await self.session.execute(count_stmt)).scalar_one()
        rows = (await self.session.execute(
            select(TestResultRow)
            .where(TestResultRow.run_id == run_id)
            .order_by(TestResultRow.created_at.asc())
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
        return list(rows), int(total)

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

    # ---- Routing Rules ----

    async def create_routing_rule(self, **kwargs: Any) -> RoutingRuleRow:
        row = RoutingRuleRow(**kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_routing_rule(self, rule_id: uuid.UUID) -> RoutingRuleRow | None:
        stmt = select(RoutingRuleRow).where(RoutingRuleRow.id == rule_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_routing_rules(self, active_only: bool = False) -> list[RoutingRuleRow]:
        stmt = select(RoutingRuleRow).order_by(RoutingRuleRow.priority)
        if active_only:
            stmt = stmt.where(RoutingRuleRow.is_active == True)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_routing_rule(self, rule_id: uuid.UUID, **kwargs: Any) -> RoutingRuleRow | None:
        stmt = select(RoutingRuleRow).where(RoutingRuleRow.id == rule_id)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        for key, value in kwargs.items():
            setattr(row, key, value)
        from datetime import datetime, timezone
        row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return row

    async def delete_routing_rule(self, rule_id: uuid.UUID) -> bool:
        stmt = select(RoutingRuleRow).where(RoutingRuleRow.id == rule_id)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    # ---- Routing Logs ----

    async def create_routing_log(self, **kwargs: Any) -> RoutingLogRow:
        row = RoutingLogRow(**kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_routing_logs(
        self,
        source_project: str | None = None,
        target_dataset: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RoutingLogRow], int]:
        stmt = select(RoutingLogRow)
        if source_project:
            stmt = stmt.where(RoutingLogRow.source_project == source_project)
        if target_dataset:
            stmt = stmt.where(RoutingLogRow.target_dataset == target_dataset)
        if status:
            stmt = stmt.where(RoutingLogRow.status == status)

        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(stmt.subquery())
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar() or 0

        stmt = stmt.order_by(RoutingLogRow.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_routing_stats(self) -> list[dict[str, Any]]:
        from sqlalchemy import case, func
        stmt = (
            select(
                RoutingLogRow.rule_id,
                func.count().label("total"),
                func.count(case((RoutingLogRow.status == "routed", 1))).label("routed"),
                func.count(case((RoutingLogRow.status == "failed", 1))).label("failed"),
                func.count(case((RoutingLogRow.status == "skipped", 1))).label("skipped"),
            )
            .group_by(RoutingLogRow.rule_id)
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        return [
            {
                "rule_id": str(r.rule_id) if r.rule_id else None,
                "total": r.total,
                "routed": r.routed,
                "failed": r.failed,
                "skipped": r.skipped,
            }
            for r in rows
        ]

    # ---- Eval case sources (ephemeral uploaded case files) ----

    async def create_eval_case_source(
        self, *, name: str, source_kind: str, file_format: str | None,
        cases: list, created_by: uuid.UUID | None = None,
    ) -> EvalCaseSourceRow:
        row = EvalCaseSourceRow(
            name=name, source_kind=source_kind, file_format=file_format,
            cases=cases, created_by=created_by,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_eval_case_source(self, source_id: uuid.UUID) -> EvalCaseSourceRow | None:
        return (await self.session.execute(
            select(EvalCaseSourceRow).where(EvalCaseSourceRow.id == source_id)
        )).scalar_one_or_none()

    async def list_eval_case_sources(
        self, *, limit: int = 50,
    ) -> list[EvalCaseSourceRow]:
        rows = (await self.session.execute(
            select(EvalCaseSourceRow)
            .order_by(EvalCaseSourceRow.created_at.desc())
            .limit(limit)
        )).scalars().all()
        return list(rows)

    # ---- Evaluator configs ----

    async def create_evaluator_config(
        self, *, name: str, tag: str | None = None,
        evaluator_type: str | None = None,
        description: str | None = None, params: dict | None = None,
        is_active: bool = True,
    ) -> EvaluatorConfigRow:
        row = EvaluatorConfigRow(
            name=name,
            tag=tag or name,  # default tag to name when caller didn't specify
            evaluator_type=evaluator_type,
            description=description,
            params=params or {},
            is_active=is_active,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_evaluator_config(self, eid: uuid.UUID) -> EvaluatorConfigRow | None:
        return (await self.session.execute(
            select(EvaluatorConfigRow).where(EvaluatorConfigRow.id == eid)
        )).scalar_one_or_none()

    async def list_evaluator_configs(
        self, *, active_only: bool = False,
    ) -> list[EvaluatorConfigRow]:
        stmt = select(EvaluatorConfigRow).order_by(EvaluatorConfigRow.created_at.desc())
        if active_only:
            stmt = stmt.where(EvaluatorConfigRow.is_active.is_(True))
        return list((await self.session.execute(stmt)).scalars().all())

    async def update_evaluator_config(
        self, eid: uuid.UUID, **updates: Any,
    ) -> EvaluatorConfigRow | None:
        row = await self.get_evaluator_config(eid)
        if row is None:
            return None
        for k, v in updates.items():
            if hasattr(row, k) and k != "id":
                setattr(row, k, v)
        row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return row

    async def delete_evaluator_config(self, eid: uuid.UUID) -> bool:
        row = await self.get_evaluator_config(eid)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True
