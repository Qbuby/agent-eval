from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from langsmith import Client

from agent_eval.data._utils import normalize_messages, to_thread, truncate
from agent_eval.models.test_case import TestCase, ToolCallExpectation

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    id: str
    name: str
    status: str
    start_time: datetime | None
    latency_s: float | None
    total_tokens: int | None
    error: str | None
    tags: list[str] = field(default_factory=list)
    input_preview: str = ""
    output_preview: str = ""


class TraceExtractor:

    def __init__(self, client: Client | None = None, **client_kwargs: Any):
        self.client = client or Client(**client_kwargs)

    async def list_runs(
        self,
        project_name: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        status: str | None = "success",
        tags: list[str] | None = None,
        limit: int = 100,
    ) -> list[RunSummary]:
        kwargs: dict[str, Any] = {
            "project_name": project_name,
            "is_root": True,
            "limit": limit,
        }
        if start_time:
            kwargs["start_time"] = start_time
        if end_time:
            kwargs["end_time"] = end_time

        filters: list[str] = []
        if status:
            filters.append(f'eq(status, "{status}")')
        if tags:
            for tag in tags:
                filters.append(f'has(tags, "{tag}")')
        if filters:
            kwargs["filter"] = " and ".join(filters) if len(filters) > 1 else filters[0]

        runs = await to_thread(self.client.list_runs, **kwargs)

        summaries = []
        for run in runs:
            input_preview = truncate(str(run.inputs or {}), 120)
            output_preview = truncate(str(run.outputs or {}), 120)
            summaries.append(
                RunSummary(
                    id=str(run.id),
                    name=run.name or "",
                    status=run.status or "unknown",
                    start_time=run.start_time,
                    latency_s=run.latency,
                    total_tokens=run.total_tokens,
                    error=run.error,
                    tags=run.tags or [],
                    input_preview=input_preview,
                    output_preview=output_preview,
                )
            )
        return summaries

    async def extract_test_cases(
        self,
        run_ids: list[str],
        *,
        source: str = "trace_derived",
        default_tags: list[str] | None = None,
        include_output_as_expected: bool = False,
    ) -> list[TestCase]:
        cases: list[TestCase] = []
        for run_id in run_ids:
            run = await to_thread(self.client.read_run, run_id=run_id)
            case = await self._run_to_test_case(
                run,
                source=source,
                default_tags=default_tags or [],
                include_output_as_expected=include_output_as_expected,
            )
            cases.append(case)
        return cases

    async def _run_to_test_case(
        self, run: Any, *, source: str, default_tags: list[str], include_output_as_expected: bool
    ) -> TestCase:
        messages = (run.inputs or {}).get("messages", [])
        if not messages:
            input_val = run.inputs or {}
            if "input" in input_val:
                messages = [{"role": "user", "content": str(input_val["input"])}]
            elif "question" in input_val:
                messages = [{"role": "user", "content": str(input_val["question"])}]
            else:
                messages = [{"role": "user", "content": str(input_val)}]

        input_messages = normalize_messages(messages)

        tool_calls = await self._extract_tool_calls(run)

        max_latency_ms = int(run.latency * 1000 * 1.5) if run.latency else None
        max_tokens = int(run.total_tokens * 1.2) if run.total_tokens else None

        case = TestCase(
            dataset_version="",
            name=f"trace-{run.name or 'run'}-{str(run.id)[:8]}",
            description=f"Extracted from run {run.id}",
            source=source,
            tags=default_tags,
            input_messages=input_messages,
            expected_tool_calls=tool_calls,
            max_latency_ms=max_latency_ms,
            max_tokens=max_tokens,
        )

        if include_output_as_expected and run.outputs:
            output_text = run.outputs.get("output", run.outputs.get("text", ""))
            if isinstance(output_text, str) and output_text:
                case.expected_output = output_text

        return case

    async def _extract_tool_calls(self, run: Any) -> list[ToolCallExpectation]:
        child_ids = getattr(run, "child_run_ids", None)
        if not child_ids:
            return []

        child_runs = await to_thread(
            self.client.list_runs,
            run_ids=child_ids,
            run_type="tool",
        )

        tool_calls = []
        for i, child in enumerate(child_runs):
            tool_calls.append(
                ToolCallExpectation(
                    tool_name=child.name or "",
                    args_matcher=child.inputs if isinstance(child.inputs, dict) else None,
                    order=i,
                    required=True,
                )
            )
        return tool_calls
