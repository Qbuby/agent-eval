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
    EvaluatorProviderRow,
    EvaluatorVersionRow,
    FeishuConversationMessageRow,
    FeishuOAuthTokenRow,
    LoopControlLogRow,
    OptimizationRow,
    RoutingLogRow,
    RoutingRuleRow,
    TestCaseRow,
    TestResultRow,
    TestRunRow,
    UserRow,
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
        langfuse_trace_name: str | None = None,
        langsmith_project: str | None = None,
        evaluator_configs: list | None = None,
        acceptance_policy: dict | None = None,
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
            langfuse_trace_name=langfuse_trace_name,
            langsmith_project=langsmith_project,
            evaluator_configs=evaluator_configs or [],
            acceptance_policy=acceptance_policy,
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

        min_pass_rate 只读取显式验收策略产生的
        summary_scores.acceptance.pass_rate；仅评分运行没有通过率，会被排除。
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
                q = q.where(
                    TestRunRow.acceptance_policy.is_not(None)
                    & (
                        TestRunRow.summary_scores["acceptance"]["pass_rate"]
                        .astext.cast(Float) >= min_pass_rate
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

    # ---- Evaluator versions (append-only snapshots) ----

    async def create_evaluator_version(
        self,
        *,
        evaluator_id: uuid.UUID,
        params: dict,
        description: str | None = None,
        created_by: uuid.UUID | None = None,
    ) -> EvaluatorVersionRow:
        """Append a new version row, monotonic version_number per evaluator.

        Race-tolerant: counts existing rows under FLUSH so concurrent saves
        will collide on the unique (evaluator_id, version_number) constraint
        and the loser raises IntegrityError — caller commits in its own
        transaction so the rollback is cheap. Real high-contention clients
        should retry once.
        """
        existing = (await self.session.execute(
            select(EvaluatorVersionRow.version_number)
            .where(EvaluatorVersionRow.evaluator_id == evaluator_id)
            .order_by(EvaluatorVersionRow.version_number.desc())
            .limit(1)
        )).scalar_one_or_none()
        next_n = (existing or 0) + 1
        row = EvaluatorVersionRow(
            evaluator_id=evaluator_id,
            version_number=next_n,
            params=params or {},
            description=description,
            created_by=created_by,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_evaluator_version(
        self, vid: uuid.UUID,
    ) -> EvaluatorVersionRow | None:
        return (await self.session.execute(
            select(EvaluatorVersionRow).where(EvaluatorVersionRow.id == vid)
        )).scalar_one_or_none()

    async def list_evaluator_versions(
        self, evaluator_id: uuid.UUID,
    ) -> list[EvaluatorVersionRow]:
        rows = (await self.session.execute(
            select(EvaluatorVersionRow)
            .where(EvaluatorVersionRow.evaluator_id == evaluator_id)
            .order_by(EvaluatorVersionRow.version_number.desc())
        )).scalars().all()
        return list(rows)

    async def set_current_evaluator_version(
        self, evaluator_id: uuid.UUID, version_id: uuid.UUID,
    ) -> EvaluatorConfigRow | None:
        """Point ``evaluator_configs.current_version_id`` at ``version_id``,
        and copy the version's params back onto the config row.

        Copying params keeps the "live" view (used by editor / list endpoints)
        cheap — no JOIN needed. If the FK is broken (version belongs to a
        different evaluator), returns None.
        """
        version = await self.get_evaluator_version(version_id)
        if version is None or version.evaluator_id != evaluator_id:
            return None
        config = await self.get_evaluator_config(evaluator_id)
        if config is None:
            return None
        config.current_version_id = version.id
        config.params = dict(version.params or {})
        config.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return config

    # ---- Evaluator providers (LLM-judge credentials) ----

    async def create_evaluator_provider(
        self,
        *,
        name: str,
        provider_type: str,
        base_url: str | None = None,
        api_key_encrypted: bytes | None = None,
        default_model: str | None = None,
        extra_config: dict | None = None,
        is_active: bool = True,
        created_by: uuid.UUID | None = None,
    ) -> EvaluatorProviderRow:
        row = EvaluatorProviderRow(
            name=name,
            provider_type=provider_type,
            base_url=base_url,
            api_key_encrypted=api_key_encrypted,
            default_model=default_model,
            extra_config=extra_config or {},
            is_active=is_active,
            created_by=created_by,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_evaluator_provider(
        self, pid: uuid.UUID,
    ) -> EvaluatorProviderRow | None:
        return (await self.session.execute(
            select(EvaluatorProviderRow).where(EvaluatorProviderRow.id == pid)
        )).scalar_one_or_none()

    async def list_evaluator_providers(
        self, *, active_only: bool = False,
    ) -> list[EvaluatorProviderRow]:
        stmt = select(EvaluatorProviderRow).order_by(
            EvaluatorProviderRow.created_at.desc()
        )
        if active_only:
            stmt = stmt.where(EvaluatorProviderRow.is_active.is_(True))
        return list((await self.session.execute(stmt)).scalars().all())

    async def update_evaluator_provider(
        self, pid: uuid.UUID, **updates: Any,
    ) -> EvaluatorProviderRow | None:
        row = await self.get_evaluator_provider(pid)
        if row is None:
            return None
        for k, v in updates.items():
            if hasattr(row, k) and k != "id":
                setattr(row, k, v)
        row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return row

    async def delete_evaluator_provider(self, pid: uuid.UUID) -> bool:
        row = await self.get_evaluator_provider(pid)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    # ── Feishu bot binding ─────────────────────────────────────────────
    # 飞书机器人：open_id ↔ user 映射。users 表不挂 TenantMixin，这些查询
    # 不受租户 ContextVar 过滤影响（机器人在无请求上下文的常驻进程里调用，
    # 正需要能按 open_id 直接定位到任意租户的 user）。

    async def get_user_by_feishu_open_id(self, open_id: str) -> UserRow | None:
        """按飞书 open_id 找已绑定的 user；未绑定返回 None。"""
        result = await self.session.execute(
            select(UserRow).where(UserRow.feishu_open_id == open_id)
        )
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: uuid.UUID) -> UserRow | None:
        result = await self.session.execute(
            select(UserRow).where(UserRow.id == user_id)
        )
        return result.scalar_one_or_none()

    async def bind_feishu_open_id(
        self, user_id: uuid.UUID, open_id: str,
    ) -> UserRow | None:
        """把 open_id 绑定到指定 user。调用方需先保证该 open_id 未被别的
        user 占用（唯一索引也会兜底：重复绑定会在 flush 时 IntegrityError）。
        user 不存在返回 None。"""
        row = await self.get_user_by_id(user_id)
        if row is None:
            return None
        row.feishu_open_id = open_id
        row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return row

    # ── Feishu user OAuth token（Bitable 访问用 user_access_token）─────────
    # 同样不受租户过滤：feishu_oauth_tokens 表不挂 TenantMixin，机器人在无请求
    # 上下文的常驻进程里按 user_id 直接取 token。加解密在 router/oauth 层做，
    # repo 只存已加密 bytes、显式带 tenant_id（表无监听器盖章）。

    async def get_feishu_oauth_token(
        self, user_id: uuid.UUID,
    ) -> FeishuOAuthTokenRow | None:
        """取某 user 的飞书 OAuth token 行；无则 None。"""
        result = await self.session.execute(
            select(FeishuOAuthTokenRow).where(
                FeishuOAuthTokenRow.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def upsert_feishu_oauth_token(
        self, user_id: uuid.UUID, **fields: Any,
    ) -> FeishuOAuthTokenRow:
        """按 user_id upsert 飞书 OAuth token。首次插入须显式带 tenant_id
        （表不挂 TenantMixin，无监听器盖章）。refresh_token 单次使用，故每次
        调用整条替换 access/refresh/两个过期时刻。flush 不 commit。"""
        row = await self.get_feishu_oauth_token(user_id)
        if row is None:
            row = FeishuOAuthTokenRow(user_id=user_id, **fields)
            self.session.add(row)
        else:
            for k, v in fields.items():
                if hasattr(row, k) and k not in ("id", "user_id"):
                    setattr(row, k, v)
            row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return row

    # ── Feishu 多轮对话历史 ────────────────────────────────────────────────
    # feishu_conversation_messages 挂 TenantMixin：写入由 before_flush 按当前
    # 租户上下文盖 tenant_id，读取被监听器自动加 tenant_id 过滤。所以这里只按
    # open_id 过滤，租户维度靠上下文兜底——调用方（bot_service）必须在正确的
    # set_tenant_context 内调用，否则跨租户串话。

    async def add_feishu_message(
        self, open_id: str, role: str, content: str,
    ) -> FeishuConversationMessageRow:
        """追加一条对话历史（role: user|assistant）。flush 不 commit。"""
        row = FeishuConversationMessageRow(
            open_id=open_id, role=role, content=content,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_recent_feishu_messages(
        self, open_id: str, limit: int = 20,
    ) -> list[FeishuConversationMessageRow]:
        """取某 open_id 最近 limit 条历史，按时间**正序**返回（可直接拼进
        LLM messages）。DB 侧按 created_at 倒序取最近 N 条，再在内存翻正。"""
        result = await self.session.execute(
            select(FeishuConversationMessageRow)
            .where(FeishuConversationMessageRow.open_id == open_id)
            .order_by(FeishuConversationMessageRow.created_at.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
        rows.reverse()
        return rows
