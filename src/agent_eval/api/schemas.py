from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateDatasetRequest(BaseModel):
    name: str
    description: str = ""
    metadata: dict[str, Any] | None = None


class DatasetResponse(BaseModel):
    id: str
    name: str
    description: str
    example_count: int
    created_at: datetime | None = None
    metadata: dict[str, Any] = {}


class VersionResponse(BaseModel):
    version_id: str
    created_at: datetime | None = None


class DatasetStatsResponse(BaseModel):
    total_cases: int
    by_source: dict[str, int]
    by_tag: dict[str, int]
    has_expected_output: int
    has_criteria: int
    has_tool_calls: int
    avg_messages_per_case: float


class TestCaseInput(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []
    source: str = "manual"
    input_messages: list[dict[str, str]]
    agent_config_override: dict[str, Any] | None = None
    expected_output: str | None = None
    expected_output_criteria: list[str] = []
    expected_tool_calls: list[dict[str, Any]] = []
    max_tool_calls: int | None = None
    max_latency_ms: int | None = None
    max_tokens: int | None = None
    scoring_mode: str = "hybrid"


class AddCasesRequest(BaseModel):
    cases: list[TestCaseInput]
    split: str | None = None


class BatchDeleteRequest(BaseModel):
    example_ids: list[str]


class GenerateScenarioRequest(BaseModel):
    dataset: str
    scenario: str
    count: int = 5
    context: str = ""
    tags: list[str] = []
    split: str | None = None
    dry_run: bool = False


class GenerateMutateRequest(BaseModel):
    dataset: str
    case_id: str
    count: int = 3
    strategy: str = "mixed"
    target_dataset: str | None = None
    tags: list[str] = []
    split: str | None = None
    dry_run: bool = False


class ListRunsRequest(BaseModel):
    project_name: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    status: str | None = "success"
    tags: list[str] | None = None
    limit: int = 100


class RunSummaryResponse(BaseModel):
    id: str
    name: str
    status: str
    start_time: datetime | None = None
    latency_s: float | None = None
    total_tokens: int | None = None
    error: str | None = None
    tags: list[str] = []
    input_preview: str = ""
    output_preview: str = ""


class ExtractRequest(BaseModel):
    run_ids: list[str]
    source: str = "trace_derived"
    default_tags: list[str] = []
    include_output_as_expected: bool = False


class ImportTracesRequest(BaseModel):
    dataset: str
    run_ids: list[str]
    source: str = "trace_derived"
    default_tags: list[str] = []
    include_output_as_expected: bool = False
    split: str | None = None


class PullDatasetRequest(BaseModel):
    source_dataset: str
    target_dataset: str | None = None
    split: str | None = None
    limit: int | None = None
