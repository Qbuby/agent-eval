from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import InstrumentedAttribute

from agent_eval.db_models.tables import (
    AgentReplyCaseStateRow,
    AgentReplyJobRow,
    AgentReplyVersionRow,
    BenchmarkCaseRow,
    CandidateCaseRow,
    DatasetMetadataRow,
    EvaluationScoreRow,
    TestResultRow,
    TestRunRow,
)
from agent_eval.omniagent_data.models import DataCapabilityError, DescribeRequest, SearchRequest

SCHEMA_VERSION = "2026-08-25.1"
SAFE_OPERATORS = frozenset(
    {"eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "starts_with", "is_null", "between"}
)


@dataclass(frozen=True)
class FieldDefinition:
    name: str
    column: InstrumentedAttribute
    data_type: str
    description: str
    operators: frozenset[str] = frozenset({"eq", "ne", "in", "is_null"})
    sortable: bool = False
    groupable: bool = False
    aggregates: frozenset[str] = frozenset()
    max_length: int = 512

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.data_type,
            "description": self.description,
            "operators": sorted(self.operators),
            "sortable": self.sortable,
            "groupable": self.groupable,
            "aggregates": sorted(self.aggregates),
            "max_length": self.max_length,
        }


@dataclass(frozen=True)
class RelationshipDefinition:
    name: str
    target: str
    key_pairs: tuple[tuple[str, str], ...]
    cardinality: str
    description: str

    def public(self, source: str) -> dict[str, Any]:
        return {
            "name": f"{source}.{self.name}",
            "path": self.name,
            "target": self.target,
            "cardinality": self.cardinality,
            "description": self.description,
        }


@dataclass(frozen=True)
class EntityDefinition:
    name: str
    model: type
    description: str
    tags: tuple[str, ...]
    examples: tuple[str, ...]
    fields: dict[str, FieldDefinition]
    relationships: dict[str, RelationshipDefinition] = field(default_factory=dict)
    source: str = "postgresql"
    required_scope: str = "data:query"

    def public(self, include_relationships: bool = True) -> dict[str, Any]:
        result = {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "required_scope": self.required_scope,
            "tags": list(self.tags),
            "fields": [value.public() for value in self.fields.values()],
            "examples": list(self.examples),
            "limits": {"rows": 100, "filter_leaves": 20, "aggregates": 5},
        }
        if include_relationships:
            result["relationships"] = [
                value.public(self.name) for value in self.relationships.values()
            ]
        return result


TEXT_OPS = frozenset({"eq", "ne", "in", "contains", "starts_with", "is_null"})
ORDER_OPS = frozenset({"eq", "ne", "lt", "lte", "gt", "gte", "in", "is_null", "between"})
NUM_AGGS = frozenset({"count", "count_distinct", "sum", "avg", "min", "max"})
ID_AGGS = frozenset({"count", "count_distinct"})


def _f(name: str, column: InstrumentedAttribute, data_type: str, description: str, **kwargs: Any) -> FieldDefinition:
    defaults: dict[str, Any] = {}
    if data_type in {"datetime", "integer", "number"}:
        defaults.update(operators=ORDER_OPS, sortable=True)
    elif data_type == "string":
        defaults.update(operators=TEXT_OPS)
    elif data_type == "uuid":
        defaults.update(aggregates=ID_AGGS)
    defaults.update(kwargs)
    return FieldDefinition(name, column, data_type, description, **defaults)


CATALOG: dict[str, EntityDefinition] = {
    "datasets": EntityDefinition(
        "datasets", DatasetMetadataRow, "Registered dataset metadata and lifecycle state.",
        ("datasets", "readiness", "governance"),
        ("List active datasets by type.", "Count archived datasets by source project."),
        {
            "id": _f("id", DatasetMetadataRow.id, "uuid", "Dataset metadata identifier."),
            "name": _f("name", DatasetMetadataRow.dataset_name, "string", "Stable dataset name.", sortable=True, groupable=True, aggregates=ID_AGGS),
            "source_project": _f("source_project", DatasetMetadataRow.source_project, "string", "Source project label.", groupable=True, aggregates=ID_AGGS),
            "dataset_type": _f("dataset_type", DatasetMetadataRow.dataset_type, "string", "candidate or conversation.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "status": _f("status", DatasetMetadataRow.status, "string", "Lifecycle state.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "created_at": _f("created_at", DatasetMetadataRow.created_at, "datetime", "Creation time."),
            "updated_at": _f("updated_at", DatasetMetadataRow.updated_at, "datetime", "Last update time."),
        },
    ),
    "candidate_cases": EntityDefinition(
        "candidate_cases", CandidateCaseRow, "Candidate cases awaiting or completing governance.",
        ("cases", "candidates", "governance"),
        ("Count pending cases per dataset.", "Find recently rejected candidate cases."),
        {
            "id": _f("id", CandidateCaseRow.id, "uuid", "Candidate identifier."),
            "dataset_name": _f("dataset_name", CandidateCaseRow.dataset_name, "string", "Owning dataset name.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "source": _f("source", CandidateCaseRow.source, "string", "How the case was created.", groupable=True, aggregates=ID_AGGS),
            "question": _f("question", CandidateCaseRow.question, "string", "Untrusted case question text.", max_length=1000),
            "category": _f("category", CandidateCaseRow.category, "string", "Governance category.", groupable=True, aggregates=ID_AGGS),
            "status": _f("status", CandidateCaseRow.status, "string", "Governance status.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "created_at": _f("created_at", CandidateCaseRow.created_at, "datetime", "Creation time."),
            "updated_at": _f("updated_at", CandidateCaseRow.updated_at, "datetime", "Last update time."),
        },
    ),
    "benchmark_cases": EntityDefinition(
        "benchmark_cases", BenchmarkCaseRow, "Curated benchmark cases used for evaluation.",
        ("cases", "benchmarks", "readiness"),
        ("Count active benchmark cases by difficulty.", "Find cases missing a category."),
        {
            "id": _f("id", BenchmarkCaseRow.id, "uuid", "Benchmark case identifier."),
            "project_id": _f("project_id", BenchmarkCaseRow.project_id, "uuid", "Owning project identifier.", groupable=True),
            "question": _f("question", BenchmarkCaseRow.question, "string", "Untrusted case question text.", max_length=1000),
            "difficulty": _f("difficulty", BenchmarkCaseRow.difficulty, "string", "Difficulty label.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "source": _f("source", BenchmarkCaseRow.source, "string", "Case source.", groupable=True, aggregates=ID_AGGS),
            "status": _f("status", BenchmarkCaseRow.status, "string", "Lifecycle status.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "created_at": _f("created_at", BenchmarkCaseRow.created_at, "datetime", "Creation time."),
            "updated_at": _f("updated_at", BenchmarkCaseRow.updated_at, "datetime", "Last update time."),
        },
    ),
    "evaluation_runs": EntityDefinition(
        "evaluation_runs", TestRunRow, "Evaluation run snapshots and completion state.",
        ("evaluations", "runs", "diagnosis"),
        ("Count failed runs by evaluation mode.", "Find long-running persisted reply evaluations."),
        {
            "id": _f("id", TestRunRow.id, "uuid", "Run identifier."),
            "status": _f("status", TestRunRow.status, "string", "Run status.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "eval_mode": _f("eval_mode", TestRunRow.eval_mode, "string", "single or comparative.", groupable=True, aggregates=ID_AGGS),
            "reply_source": _f("reply_source", TestRunRow.reply_source, "string", "live or persisted replies.", groupable=True, aggregates=ID_AGGS),
            "started_at": _f("started_at", TestRunRow.started_at, "datetime", "Execution start time."),
            "finished_at": _f("finished_at", TestRunRow.finished_at, "datetime", "Execution finish time."),
            "created_at": _f("created_at", TestRunRow.created_at, "datetime", "Creation time."),
        },
        relationships={
            "results": RelationshipDefinition("results", "evaluation_results", (("id", "run_id"),), "one_to_many", "Per-case results in this run."),
        },
    ),
    "evaluation_results": EntityDefinition(
        "evaluation_results", TestResultRow, "Per-case execution facts and bounded error/token metrics.",
        ("evaluations", "results", "failures", "tokens"),
        ("Group failed results by error type.", "Find high-token results with tool calls."),
        {
            "id": _f("id", TestResultRow.id, "uuid", "Result identifier."),
            "run_id": _f("run_id", TestResultRow.run_id, "uuid", "Parent run identifier.", groupable=True),
            "status": _f("status", TestResultRow.status, "string", "Execution/result status.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "error_type": _f("error_type", TestResultRow.error_type, "string", "Bounded error class.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "error_message": _f("error_message", TestResultRow.error_message, "string", "Untrusted bounded error text.", max_length=500),
            "latency_ms": _f("latency_ms", TestResultRow.latency_ms, "integer", "End-to-end latency in milliseconds.", groupable=False, aggregates=NUM_AGGS),
            "total_tokens": _f("total_tokens", TestResultRow.total_tokens, "integer", "Total model tokens.", aggregates=NUM_AGGS),
            "prompt_tokens": _f("prompt_tokens", TestResultRow.prompt_tokens, "integer", "Prompt tokens.", aggregates=NUM_AGGS),
            "completion_tokens": _f("completion_tokens", TestResultRow.completion_tokens, "integer", "Completion tokens.", aggregates=NUM_AGGS),
            "tool_call_count": _f("tool_call_count", TestResultRow.tool_call_count, "integer", "Tool calls made by the agent.", aggregates=NUM_AGGS),
            "reply_version_id": _f("reply_version_id", TestResultRow.reply_version_id, "uuid", "Persisted reply version used by this result.", groupable=True),
            "created_at": _f("created_at", TestResultRow.created_at, "datetime", "Creation time."),
        },
        relationships={
            "scores": RelationshipDefinition("scores", "evaluation_scores", (("id", "result_id"),), "one_to_many", "Evaluator scores for this result."),
        },
    ),
    "evaluation_scores": EntityDefinition(
        "evaluation_scores", EvaluationScoreRow, "Safe scalar evaluator scores by dimension.",
        ("evaluations", "scores", "quality"),
        ("Average score by dimension.", "Find dimensions with the lowest weighted score."),
        {
            "id": _f("id", EvaluationScoreRow.id, "uuid", "Score identifier."),
            "result_id": _f("result_id", EvaluationScoreRow.result_id, "uuid", "Parent result identifier.", groupable=True),
            "dimension": _f("dimension", EvaluationScoreRow.dimension, "string", "Evaluator dimension key.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "score": _f("score", EvaluationScoreRow.score, "number", "Raw scalar score.", aggregates=NUM_AGGS),
            "weight": _f("weight", EvaluationScoreRow.weight, "number", "Configured score weight.", aggregates=NUM_AGGS),
            "weighted_score": _f("weighted_score", EvaluationScoreRow.weighted_score, "number", "Weighted scalar score.", aggregates=NUM_AGGS),
            "scoring_method": _f("scoring_method", EvaluationScoreRow.scoring_method, "string", "Scoring method label.", groupable=True, aggregates=ID_AGGS),
            "created_at": _f("created_at", EvaluationScoreRow.created_at, "datetime", "Creation time."),
        },
    ),
    "reply_jobs": EntityDefinition(
        "reply_jobs", AgentReplyJobRow, "Durable reply-generation jobs and progress counters.",
        ("replies", "jobs", "readiness"),
        ("Find failed reply jobs by dataset.", "Compare generated reply success counts."),
        {
            "id": _f("id", AgentReplyJobRow.id, "uuid", "Reply job identifier."),
            "dataset_type": _f("dataset_type", AgentReplyJobRow.dataset_type, "string", "Dataset source type.", groupable=True, aggregates=ID_AGGS),
            "dataset_name": _f("dataset_name", AgentReplyJobRow.dataset_name, "string", "Dataset name.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "status": _f("status", AgentReplyJobRow.status, "string", "Job status.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "total_count": _f("total_count", AgentReplyJobRow.total_count, "integer", "Total cases.", aggregates=NUM_AGGS),
            "succeeded_count": _f("succeeded_count", AgentReplyJobRow.succeeded_count, "integer", "Succeeded cases.", aggregates=NUM_AGGS),
            "failed_count": _f("failed_count", AgentReplyJobRow.failed_count, "integer", "Failed cases.", aggregates=NUM_AGGS),
            "running_count": _f("running_count", AgentReplyJobRow.running_count, "integer", "Currently running cases.", aggregates=NUM_AGGS),
            "cancel_requested": _f("cancel_requested", AgentReplyJobRow.cancel_requested, "boolean", "Whether cancellation was requested."),
            "finished_at": _f("finished_at", AgentReplyJobRow.finished_at, "datetime", "Finish time."),
            "created_at": _f("created_at", AgentReplyJobRow.created_at, "datetime", "Creation time."),
            "updated_at": _f("updated_at", AgentReplyJobRow.updated_at, "datetime", "Last update time."),
        },
        relationships={
            "versions": RelationshipDefinition("versions", "reply_versions", (("id", "job_id"),), "one_to_many", "Reply versions produced by this job."),
        },
    ),
    "reply_versions": EntityDefinition(
        "reply_versions", AgentReplyVersionRow, "Safe reply-version metadata without reply content or agent credentials.",
        ("replies", "versions"),
        ("Count succeeded reply versions by dataset.", "Find edited versions with high token use."),
        {
            "id": _f("id", AgentReplyVersionRow.id, "uuid", "Reply version identifier."),
            "dataset_type": _f("dataset_type", AgentReplyVersionRow.dataset_type, "string", "Dataset source type.", groupable=True, aggregates=ID_AGGS),
            "case_ref": _f("case_ref", AgentReplyVersionRow.case_ref, "string", "Logical case reference.", groupable=True, aggregates=ID_AGGS),
            "dataset_name": _f("dataset_name", AgentReplyVersionRow.dataset_name, "string", "Dataset name.", groupable=True, aggregates=ID_AGGS),
            "version_number": _f("version_number", AgentReplyVersionRow.version_number, "integer", "Per-case version number.", aggregates=NUM_AGGS),
            "version_label": _f("version_label", AgentReplyVersionRow.version_label, "string", "User-visible version label.", groupable=True, aggregates=ID_AGGS),
            "status": _f("status", AgentReplyVersionRow.status, "string", "Version status.", groupable=True, sortable=True, aggregates=ID_AGGS),
            "latency_ms": _f("latency_ms", AgentReplyVersionRow.latency_ms, "integer", "Generation latency.", aggregates=NUM_AGGS),
            "total_tokens": _f("total_tokens", AgentReplyVersionRow.total_tokens, "integer", "Generation tokens.", aggregates=NUM_AGGS),
            "edited": _f("edited", AgentReplyVersionRow.edited, "boolean", "Whether a human edited this version.", groupable=True),
            "job_id": _f("job_id", AgentReplyVersionRow.job_id, "uuid", "Generating job identifier.", groupable=True),
            "created_at": _f("created_at", AgentReplyVersionRow.created_at, "datetime", "Creation time."),
            "updated_at": _f("updated_at", AgentReplyVersionRow.updated_at, "datetime", "Last update time."),
        },
    ),
    "reply_case_states": EntityDefinition(
        "reply_case_states", AgentReplyCaseStateRow, "Per-case pointer to the current reply version.",
        ("replies", "readiness", "cases"),
        ("Count cases missing a current reply by dataset.",),
        {
            "id": _f("id", AgentReplyCaseStateRow.id, "uuid", "Case state identifier."),
            "dataset_type": _f("dataset_type", AgentReplyCaseStateRow.dataset_type, "string", "Dataset source type.", groupable=True, aggregates=ID_AGGS),
            "case_ref": _f("case_ref", AgentReplyCaseStateRow.case_ref, "string", "Logical case reference.", groupable=True, aggregates=ID_AGGS),
            "dataset_name": _f("dataset_name", AgentReplyCaseStateRow.dataset_name, "string", "Dataset name.", groupable=True, aggregates=ID_AGGS),
            "current_version_id": _f("current_version_id", AgentReplyCaseStateRow.current_version_id, "uuid", "Current reply version, null when missing.", groupable=True),
            "created_at": _f("created_at", AgentReplyCaseStateRow.created_at, "datetime", "Creation time."),
            "updated_at": _f("updated_at", AgentReplyCaseStateRow.updated_at, "datetime", "Last update time."),
        },
    ),
}


def search_catalog(request: SearchRequest) -> dict[str, Any]:
    query_terms = set(request.query.lower().replace("_", " ").split())
    scored: list[tuple[int, EntityDefinition]] = []
    for entity in CATALOG.values():
        if request.source and entity.source != request.source:
            continue
        if request.tags and not set(request.tags).intersection(entity.tags):
            continue
        haystack = " ".join((entity.name, entity.description, *entity.tags, *entity.examples)).lower()
        score = sum(3 if term in entity.name else 1 for term in query_terms if term in haystack)
        if score:
            scored.append((score, entity))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return {
        "schema_version": SCHEMA_VERSION,
        "items": [
            {
                "name": entity.name,
                "description": entity.description,
                "source": entity.source,
                "tags": list(entity.tags),
                "examples": list(entity.examples),
                "relationships": [rel.public(entity.name) for rel in entity.relationships.values()],
                "enabled": True,
            }
            for _, entity in scored[: request.limit]
        ],
    }


def describe_entities(request: DescribeRequest) -> dict[str, Any]:
    missing = [name for name in request.entities if name not in CATALOG]
    if missing:
        raise DataCapabilityError("ENTITY_NOT_FOUND", f"unknown entities: {', '.join(missing)}")
    return {
        "schema_version": SCHEMA_VERSION,
        "entities": [CATALOG[name].public(request.include_relationships) for name in request.entities],
    }


def validate_catalog_config() -> None:
    root = Path(__file__).resolve().parents[3]
    path = root / "config" / "omniagent-data-catalog.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    configured = {item["name"]: item for item in data.get("entities", [])}
    if set(configured) != set(CATALOG):
        raise RuntimeError("OmniAgent data catalog entity names do not match runtime registry")
    for name, entity in CATALOG.items():
        public_fields = set(configured[name].get("fields", []))
        if public_fields != set(entity.fields):
            raise RuntimeError(f"OmniAgent data catalog fields do not match for {name}")
        for field_def in entity.fields.values():
            if not field_def.operators <= SAFE_OPERATORS:
                raise RuntimeError(f"unsupported operator registered for {name}.{field_def.name}")
