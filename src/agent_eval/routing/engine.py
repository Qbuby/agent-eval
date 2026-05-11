from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select

from agent_eval.config_service import config_service
from agent_eval.data.trace_extractor import TraceExtractor
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import RoutingLogRow, RoutingRuleRow
from agent_eval.routing.matcher import RuleMatcher

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class RoutingEngine:
    def __init__(
        self,
        extractor: TraceExtractor,
        provider: Any,
        matcher: RuleMatcher | None = None,
    ):
        self._extractor = extractor
        self._provider = provider
        self._matcher = matcher or RuleMatcher()

    async def process_runs(self, project_name: str, runs: list[dict]) -> list[dict]:
        results = []
        rules = await self._load_active_rules()

        for run in runs:
            result = await self._route_single_run(project_name, run, rules)
            results.append(result)

        return results

    async def process_with_retry(self, project_name: str, runs: list[dict]) -> list[dict]:
        results = []
        rules = await self._load_active_rules()

        for run in runs:
            result = await self._route_with_retry(project_name, run, rules, attempt=0)
            results.append(result)

        return results

    async def _route_single_run(
        self, project_name: str, run: dict, rules: list[RoutingRuleRow]
    ) -> dict:
        run_id = run.get("id", str(uuid.uuid4()))

        matched_rule = None
        for rule in rules:
            if self._matcher.matches(rule, run, project_name):
                matched_rule = rule
                break

        if matched_rule is None:
            default_dataset = await config_service.get("routing.default_dataset")
            if not default_dataset:
                await self._log_routing_safe(
                    rule_id=None,
                    run_id=str(run_id),
                    source_project=project_name,
                    target_dataset=None,
                    status="skipped",
                )
                return {"run_id": str(run_id), "status": "skipped", "reason": "no_matching_rule"}

            try:
                await self._extract_and_write(run, default_dataset, {})
                await self._log_routing_safe(
                    rule_id=None,
                    run_id=str(run_id),
                    source_project=project_name,
                    target_dataset=default_dataset,
                    status="routed",
                )
                return {"run_id": str(run_id), "status": "routed", "dataset": default_dataset, "rule": None}
            except Exception as e:
                await self._log_routing_safe(
                    rule_id=None,
                    run_id=str(run_id),
                    source_project=project_name,
                    target_dataset=default_dataset,
                    status="failed",
                    error_message=str(e),
                )
                return {"run_id": str(run_id), "status": "failed", "error": str(e)}

        try:
            await self._extract_and_write(
                run, matched_rule.target_dataset, matched_rule.transform_config or {}
            )
            await self._log_routing_safe(
                rule_id=matched_rule.id,
                run_id=str(run_id),
                source_project=project_name,
                target_dataset=matched_rule.target_dataset,
                status="routed",
            )
            return {
                "run_id": str(run_id),
                "status": "routed",
                "dataset": matched_rule.target_dataset,
                "rule": str(matched_rule.id),
            }
        except Exception as e:
            logger.error("Routing failed for run %s: %s", run_id, e)
            await self._log_routing_safe(
                rule_id=matched_rule.id,
                run_id=str(run_id),
                source_project=project_name,
                target_dataset=matched_rule.target_dataset,
                status="failed",
                error_message=str(e),
            )
            return {"run_id": str(run_id), "status": "failed", "error": str(e)}

    async def _extract_and_write(
        self, run: dict, dataset_name: str, transform_config: dict
    ) -> None:
        run_id = str(run.get("id", ""))
        include_output = transform_config.get("include_output_as_expected", False)
        default_tags = transform_config.get("default_tags", [])

        cases = await self._extractor.extract_test_cases(
            run_ids=[run_id],
            source="routing",
            default_tags=default_tags,
            include_output_as_expected=include_output,
        )

        if cases:
            await self._provider.add_cases_batch(
                dataset_name=dataset_name,
                cases=cases,
                split=transform_config.get("split"),
            )

    async def _load_active_rules(self) -> list[RoutingRuleRow]:
        async with async_session_factory() as session:
            stmt = (
                select(RoutingRuleRow)
                .where(RoutingRuleRow.is_active == True)
                .order_by(RoutingRuleRow.priority)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def _log_routing_safe(
        self,
        rule_id: uuid.UUID | None,
        run_id: str,
        source_project: str,
        target_dataset: str | None,
        status: str,
        error_message: str | None = None,
    ) -> None:
        try:
            async with async_session_factory() as session:
                row = RoutingLogRow(
                    rule_id=rule_id,
                    run_id=run_id,
                    source_project=source_project,
                    target_dataset=target_dataset,
                    status=status,
                    error_message=error_message,
                )
                session.add(row)
                await session.commit()
        except Exception as e:
            logger.warning("Failed to write routing log for run %s: %s", run_id, e)

    async def _route_with_retry(
        self, project_name: str, run: dict, rules: list[RoutingRuleRow], attempt: int
    ) -> dict:
        result = await self._route_single_run(project_name, run, rules)

        if result["status"] == "failed" and attempt < MAX_RETRIES - 1:
            logger.info(
                "Retrying run %s (attempt %d/%d)", run.get("id"), attempt + 2, MAX_RETRIES
            )
            await asyncio.sleep(2 ** attempt)
            return await self._route_with_retry(project_name, run, rules, attempt + 1)

        return result
