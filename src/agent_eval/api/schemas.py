from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateDatasetRequest(BaseModel):
    name: str
    description: str = ""
    metadata: dict[str, Any] | None = None
    source_project: str | None = None


class DatasetResponse(BaseModel):
    id: str
    name: str
    description: str
    example_count: int
    created_at: datetime | None = None
    metadata: dict[str, Any] = {}
    source_project: str | None = None


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
    input_messages: list[dict[str, Any]]
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
    test_scenario: str = Field(
        description="测试场景: faithfulness, context_recall, answer_relevancy, "
        "context_precision, context_relevancy, hallucination"
    )
    case_category: str = Field(
        default="normal",
        description="样例类别: normal, bad_case, edge_case"
    )
    count: int = 5
    context: str = ""
    dry_run: bool = True


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
    limit: int = Field(default=50, le=100)
    page: int = 1
    page_size: int = 20


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
    model_name: str = ""
    first_token_s: float | None = None
    first_tool_call_s: float | None = None


class ExtractRequest(BaseModel):
    run_ids: list[str]
    source: str = "trace_derived"
    default_tags: list[str] = []
    include_output_as_expected: bool = False


class ImportTracesRequest(BaseModel):
    dataset: str
    run_ids: list[str]
    project_name: str | None = None
    source: str = "trace_derived"
    default_tags: list[str] = []
    include_output_as_expected: bool = False
    split: str | None = None


class PullDatasetRequest(BaseModel):
    source_dataset: str
    target_dataset: str | None = None
    split: str | None = None
    limit: int | None = None


class RunDetailRequest(BaseModel):
    run_id: str
    project_name: str | None = None


class FillModelsRequest(BaseModel):
    project_name: str
    # Each entry is (run_id, start_time). start_time is optional; if absent for
    # all entries the backend will have to read_run each root (slow fallback).
    runs: list[dict[str, Any]] = Field(
        description="Each item: {id: str, start_time: ISO-8601 string | null}"
    )


class FillModelsResponse(BaseModel):
    models: dict[str, str]
    # first_tool_calls: per-root-id seconds from run start to the first tool
    # child's start_time. Populated by the same enrich walk; absent for roots
    # that made no tool call.
    first_tool_calls: dict[str, float] = {}
    missing: list[str] = []


# ─── Evaluation (Langfuse-backed) ───

class EvalAgentConfig(BaseModel):
    """Agent endpoint to evaluate against. Mirrors agent_adapter.py contracts."""
    type: str  # "openai" | "sse"
    url: str
    api_key: str = ""
    model: str = "default"
    headers: dict[str, str] = {}
    payload_template: dict[str, Any] = {}
    timeout: float = 120.0


class EvaluatorConfig(BaseModel):
    """One evaluator activated for a run."""
    name: str  # "exact_match" | "llm_judge" | "tool_sequence_match"
    params: dict[str, Any] = {}  # e.g. llm_judge: {prompt_template, dimensions}


class StartEvalRequest(BaseModel):
    # Source: exactly one of these three should be set.
    benchmark_version_id: str | None = None
    project_id: str | None = None                # use all benchmark_cases of a project
    case_source_id: str | None = None            # uploaded file (eval_case_sources.id)
    # Sample selection for benchmark-backed sources:
    case_ids: list[str] | None = None
    filter_tags: list[str] | None = None
    filter_category_id: str | None = None
    limit: int | None = None

    agent: EvalAgentConfig
    # Evaluator instances by id (evaluator_configs table). Empty list is not allowed.
    evaluator_ids: list[str] = Field(default_factory=list)
    concurrency: int = Field(default=3, ge=1, le=20)
    run_name: str | None = None
    # LangSmith project where the agent will write its own trace. The
    # evaluation service uses this to backfill test_results.langsmith_run_id
    # after the agent call completes. Leave blank to skip backfill.
    langsmith_project: str | None = None


class EvalRunSummary(BaseModel):
    """Row in the run-history list."""
    id: str
    benchmark_version_id: str | None = None
    status: str  # pending | running | completed | failed | stopping | interrupted
    started_at: datetime | None = None
    finished_at: datetime | None = None
    langfuse_run_name: str | None = None
    langsmith_project: str | None = None
    agent_config: dict[str, Any] = {}
    summary_scores: dict[str, Any] | None = None
    progress: dict[str, int] = {}  # {total, completed, failed} — populated for running
    created_at: datetime | None = None


class EvalRunDetail(EvalRunSummary):
    evaluator_configs: list[dict[str, Any]] = []


class EvalResultRow(BaseModel):
    id: str
    benchmark_case_id: str | None = None
    test_case_id: str | None = None
    status: str
    actual_output: str | None = None
    question: str | None = None
    latency_ms: int | None = None
    total_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    tool_call_count: int | None = None
    error_message: str | None = None
    langfuse_trace_id: str | None = None
    langsmith_run_id: str | None = None
    scores: dict[str, float] = {}  # dimension -> score


class EvalResultsPage(BaseModel):
    items: list[EvalResultRow]
    total: int
    page: int
    page_size: int


class BuiltinEvaluator(BaseModel):
    name: str
    description: str
    params_schema: dict[str, Any] = {}  # JSON-schema-ish for UI to render


# ─── Evaluator instances (named, reusable) ───

class EvaluatorInstance(BaseModel):
    id: str
    name: str
    # The literal string we stamp onto every sample's Langfuse trace.
    # Defaults to name when not specified, but the user can override
    # (e.g. name="goal accuracy v2" + tag="agent-eval-correctness").
    tag: str = ""
    # Kept for back-compat with historical runs that referenced one of
    # the old built-in scoring functions. New evaluators don't need it.
    evaluator_type: str | None = None
    description: str | None = None
    params: dict[str, Any] = {}
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CreateEvaluatorRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    tag: str | None = Field(default=None, max_length=128)
    evaluator_type: str | None = None
    description: str | None = None
    params: dict[str, Any] = {}
    is_active: bool = True


class UpdateEvaluatorRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    tag: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    params: dict[str, Any] | None = None
    is_active: bool | None = None


# ─── Uploaded case sources ───

class UploadCasesResponse(BaseModel):
    source_id: str
    name: str
    count: int
    preview: list[dict[str, Any]]  # first N cases for display


class EvalCaseSourceSummary(BaseModel):
    id: str
    name: str
    source_kind: str
    file_format: str | None = None
    count: int
    created_at: datetime | None = None


class RunChildMeta(BaseModel):
    id: str
    name: str
    run_type: str
    status: str
    start_time: datetime | None = None
    latency_s: float | None = None
    total_tokens: int | None = None
    error: str | None = None
    has_children: bool = False


class RunDetailResponse(BaseModel):
    id: str
    name: str
    run_type: str
    status: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    latency_s: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    error: str | None = None
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    extra: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    tags: list[str] = []
    parent_run_id: str | None = None
    trace_id: str | None = None
    children: list[RunChildMeta] = []
    children_truncated: bool = False
