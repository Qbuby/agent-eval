from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


# 数据集类型：区分用途不同的两类数据集，避免在各自页面里互相串。
# - candidate    备选数据集（单轮问答样例，老数据无标记一律按此处理，向后兼容）
# - conversation 多轮对话集（多轮对话样例，固定 thread_id 逐轮调用）
_DATASET_TYPES = {"candidate", "conversation"}
DEFAULT_DATASET_TYPE = "candidate"


class CreateDatasetRequest(BaseModel):
    name: str
    description: str = ""
    metadata: dict[str, Any] | None = None
    source_project: str | None = None
    dataset_type: str = DEFAULT_DATASET_TYPE

    @field_validator("dataset_type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in _DATASET_TYPES:
            raise ValueError(f"dataset_type 非法：{v!r}，须为 {sorted(_DATASET_TYPES)} 之一")
        return v


class DatasetResponse(BaseModel):
    id: str
    name: str
    description: str
    example_count: int
    created_at: datetime | None = None
    metadata: dict[str, Any] = {}
    source_project: str | None = None
    dataset_type: str = DEFAULT_DATASET_TYPE


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


# 多轮对话：合法 message role 白名单。多轮样例必须明确角色语义，
# 故在入口层收紧（此前完全无校验）。tool 角色保留给 function/tool 结果消息。
_ALLOWED_ROLES = {"user", "assistant", "system", "tool"}


class TurnExpectationInput(BaseModel):
    """单轮（user→assistant）的期望。turn_index 指向 input_messages 中
    被评测的 assistant 轮下标（从 0 计）。本期只录入，第二期评估消费。"""

    turn_index: int
    criteria: list[str] = []
    expected_output: str | None = None


class TestCaseInput(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []
    # 受管单值类别名（多轮对话集用，对齐基准测试集的类别）。空=不指定。
    category: str | None = None
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
    # —— 多轮对话扩展（向后兼容：单轮样例不填即可）——
    conversation_goal: str | None = None
    turn_expectations: list[TurnExpectationInput] = []

    @field_validator("input_messages")
    @classmethod
    def _check_messages(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not v:
            raise ValueError("input_messages 不能为空")
        for i, m in enumerate(v):
            role = m.get("role")
            if role not in _ALLOWED_ROLES:
                raise ValueError(
                    f"消息 #{i + 1} role 非法：{role!r}，须为 {sorted(_ALLOWED_ROLES)} 之一"
                )
            if not isinstance(m.get("content"), str):
                raise ValueError(f"消息 #{i + 1} content 必须是字符串")
        return v


class AddCasesRequest(BaseModel):
    cases: list[TestCaseInput]
    split: str | None = None


class BatchDeleteRequest(BaseModel):
    example_ids: list[str]


class GenerateScenarioRequest(BaseModel):
    dataset: str
    test_scenario: str = Field(
        default="",
        description="测试场景/主题（可选，自由文本）。留空则让 agent 围绕其核心领域能力自由出题",
    )
    case_category: str = Field(
        default="normal",
        description="样例类别: normal, bad_case, edge_case"
    )
    count: int = 5
    context: str = ""
    dry_run: bool = True
    agent_endpoint_url: str | None = Field(
        default=None,
        description="被测 agent 端点 URL（可选）。留空则用 target_agent.endpoint_url 默认；"
        "传入时须为已配置的 endpoint 预设之一，api_key/timeout/type 仍取共享 target_agent.* 配置",
    )
    run_agent: bool = Field(
        default=False,
        description="生成后是否让被测 agent 实跑一遍问题、用真实回答覆盖 expected_output（多轮逐轮回填）。默认关，开启会成倍增加耗时。",
    )


class GenerateMutateRequest(BaseModel):
    dataset: str
    case_id: str
    count: int = 3
    strategy: str = "mixed"
    target_dataset: str | None = None
    tags: list[str] = []
    split: str | None = None
    dry_run: bool = False
    agent_endpoint_url: str | None = Field(
        default=None,
        description="被测 agent 端点 URL（可选）。留空则用 target_agent.endpoint_url 默认",
    )
    run_agent: bool = Field(
        default=False,
        description="生成后是否让被测 agent 实跑一遍问题、用真实回答覆盖 expected_output（多轮逐轮回填）。默认关，开启会成倍增加耗时。",
    )


class ListRunsRequest(BaseModel):
    project_name: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    status: str | None = "success"
    tags: list[str] | None = None
    limit: int = Field(default=50, le=100)
    page: int = 1
    page_size: int = 20
    # Whether to fetch LLM child runs to populate `model_name` on each row.
    # False by default because the extra LangSmith query roughly doubles cold
    # latency (50-80s on large projects). The dedicated /fill_models endpoint
    # exists for that, and the frontend already exposes a "补齐信息" button.
    enrich_models: bool = False
    # Whether to ask LangSmith for inputs/outputs at all. True by default
    # because LangSmith projects often don't populate inputs_preview/
    # outputs_preview, so leaving it false yields an empty preview column.
    # Set false for warm-up calls or scenarios that don't need previews.
    with_io: bool = True


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


class AcceptanceCriterion(BaseModel):
    evaluator_id: str
    evaluator_version_id: str | None = None
    dimension_key: str
    direction: str
    threshold: float
    reducer: str = "conversation_or_mean"


class AcceptanceRunRule(BaseModel):
    min_case_pass_rate: float = Field(ge=0.0, le=1.0)
    min_decision_coverage: float = Field(ge=0.0, le=1.0)


class AcceptancePolicy(BaseModel):
    version: int = 1
    mode: str = "threshold"
    case_rule: str = "all"
    criteria: list[AcceptanceCriterion] = Field(min_length=1)
    run_rule: AcceptanceRunRule


class StartEvalRequest(BaseModel):
    # Source: exactly one of these four should be set.
    benchmark_version_id: str | None = None
    project_id: str | None = None                # use all benchmark_cases of a project
    case_source_id: str | None = None            # uploaded file (eval_case_sources.id)
    # 多轮对话集（dataset_type=conversation）的 LangSmith 数据集名。设置后走
    # 多轮回放评估通路：固定 thread_id 逐轮喂 user 消息，逐轮期望 + 对话级目标
    # 双重打分。与上面三种单轮源互斥。
    conversation_dataset: str | None = None
    # Sample selection for benchmark-backed sources:
    case_ids: list[str] | None = None
    filter_tags: list[str] | None = None
    filter_category_id: str | None = None
    limit: int | None = None

    agent: EvalAgentConfig
    # 评估模式：single（缺省，单模）| comparative（双模对比）。comparative 时
    # agent_b 必填，两 agent 并发跑同一样例，评估器单次对比打分。
    eval_mode: str = "single"
    # 双模对比的 B 侧 agent 配置；single 模式忽略。
    agent_b: EvalAgentConfig | None = None
    # Evaluator instances by id (evaluator_configs table). Empty list is not allowed.
    evaluator_ids: list[str] = Field(default_factory=list)
    # 缺省为 None：仅评分，不产生隐式通过/失败结论。
    acceptance_policy: AcceptancePolicy | None = None
    concurrency: int = Field(default=3, ge=1, le=20)
    run_name: str | None = None
    # LangSmith project where the agent will write its own trace. The
    # evaluation service uses this to backfill test_results.langsmith_run_id
    # after the agent call completes. Leave blank to skip backfill.
    langsmith_project: str | None = None
    # Langfuse trace name where the agent writes its own trace. Symmetric to
    # langsmith_project: after the run settles, the service pulls Langfuse
    # traces by (name, time-window), matches each by question text, and
    # backfills test_results.langfuse_trace_id. Leave blank to skip.
    langfuse_trace_name: str | None = None
    # 飞书完成通知目标 open_id 列表（机器人触发评估时注入触发者；与全局固定
    # 接收者合并去重）。HTTP/UI 触发通常留空——UI 用户无飞书身份。
    notify_open_ids: list[str] = Field(default_factory=list)


class EvalRunSummary(BaseModel):
    """Row in the run-history list."""
    id: str
    benchmark_version_id: str | None = None
    status: str  # pending | running | completed | failed | stopping | interrupted
    started_at: datetime | None = None
    finished_at: datetime | None = None
    langfuse_run_name: str | None = None
    langfuse_trace_name: str | None = None
    langsmith_project: str | None = None
    agent_config: dict[str, Any] = {}
    # 双模对比：模式标记 + B 侧 agent 配置快照。single 模式下 eval_mode='single'、
    # agent_config_b=None。前端据 eval_mode 切换对比渲染。
    eval_mode: str = "single"
    agent_config_b: dict[str, Any] | None = None
    acceptance_policy: dict[str, Any] | None = None
    summary_scores: dict[str, Any] | None = None
    facts: dict[str, Any] = {}
    acceptance: dict[str, Any] = {}
    progress: dict[str, int] = {}  # {total, completed, failed} — populated for running
    created_at: datetime | None = None


class EvalRunDetail(EvalRunSummary):
    evaluator_configs: list[dict[str, Any]] = []


class EvalResultRow(BaseModel):
    id: str
    benchmark_case_id: str | None = None
    test_case_id: str | None = None
    # status 保留原始持久化值以兼容导出；以下三字段是当前只读语义投影。
    status: str
    execution_status: str = "unknown"
    evaluation_status: str = "unknown"
    acceptance_decision: str | None = None
    decision_source: str = "current"
    criterion_results: list[dict[str, Any]] = []
    actual_output: str | None = None
    question: str | None = None
    latency_ms: int | None = None
    total_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    tool_call_count: int | None = None
    # Time-to-first-token, milliseconds since invoke. Both NULL on adapters
    # that don't expose intermediate stream events (OpenAI non-streaming).
    first_thinking_token_ms: int | None = None
    first_answer_token_ms: int | None = None
    # List of {tool_name, args, output} captured during the agent call.
    # Surfaced so the UI can render per-case tool-call detail without a
    # second round-trip to LangSmith.
    actual_tool_calls: list[dict[str, Any]] | None = None
    # Ordered CoT timeline captured from the SSE stream:
    #   {"steps": [{type:"thought"|"tool_call"|"answer", ...}, ...]}
    # ``None`` for legacy rows or for non-SSE adapters that have no CoT.
    full_trace: dict[str, Any] | None = None
    error_message: str | None = None
    error_type: str | None = None
    langfuse_trace_id: str | None = None
    langsmith_run_id: str | None = None
    attempts_made: int = 1
    scores: dict[str, float] = {}  # dimension -> score
    # 逐分数项的打分明细：dimension -> {reasoning?, checks?}。checks 为
    # checklist 评估器的逐条判定 [{id,desc,verdict,evidence}]，支撑可溯源打分链路。
    # 详情页据此逐条渲染 ✓/✗/— + 证据。无明细的分数项此处缺省。
    score_details: dict[str, dict[str, Any]] = {}
    # 双模对比结果（agent_b 回复 + 逐维度 A/B 分 + winner + 整体 winner）。
    # 单模为 None。结构见 comparison JSONB（langfuse_runner._run_comparative_case）。
    comparison: dict[str, Any] | None = None


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
    # Pointer to the active version row (None for legacy / unversioned
    # evaluators). The frontend uses it to highlight the active row in
    # the versions tab.
    current_version_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EvaluatorVersion(BaseModel):
    """One snapshot of an evaluator's params at a point in time."""
    id: str
    evaluator_id: str
    version_number: int
    params: dict[str, Any] = {}
    description: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None


class CreateEvaluatorVersionRequest(BaseModel):
    """Body for `POST /evaluators/{id}/versions` — appends a new snapshot.

    ``activate`` defaults to True so the common "Save" path immediately
    routes future invocations to this version. Set False for "Save as
    draft" workflows that the UI may add later.
    """
    params: dict[str, Any] = {}
    description: str | None = None
    activate: bool = True


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


# ─── Evaluator providers (LLM-judge credentials) ───

# Provider types we know how to call. Stored as a free string in DB so
# new types can be added without a migration; the API validates the
# enum on create/update.
ALLOWED_PROVIDER_TYPES = (
    "openai",
    "openai_compatible",
    "anthropic",
    "deepseek",
    "azure",
    "custom",
    # agent：不调 LLM API，而是直接 SSE 连一个 agent 端点（典型是被测的
    # LangGraph v2 目标 agent），把 agent 的回复当作 judge 输出再解析出分。
    # base_url 存 SSE URL；extra_config 可放 mode/language/headers/payload_template。
    "agent",
)


class EvaluatorProviderResponse(BaseModel):
    id: str
    name: str
    provider_type: str
    base_url: str | None = None
    default_model: str | None = None
    extra_config: dict[str, Any] = {}
    is_active: bool
    has_api_key: bool = False
    api_key_masked: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CreateEvaluatorProviderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    provider_type: str = Field(min_length=1, max_length=32)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    default_model: str | None = Field(default=None, max_length=128)
    extra_config: dict[str, Any] = {}
    is_active: bool = True


class UpdateEvaluatorProviderRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    provider_type: str | None = Field(default=None, min_length=1, max_length=32)
    base_url: str | None = Field(default=None, max_length=500)
    # api_key semantics:
    #   omitted (field unset)  -> keep existing ciphertext
    #   ""                     -> clear the stored key
    #   "<value>"              -> re-encrypt and replace
    api_key: str | None = Field(default=None, max_length=500)
    default_model: str | None = Field(default=None, max_length=128)
    extra_config: dict[str, Any] | None = None
    is_active: bool | None = None


class TestProviderResponse(BaseModel):
    ok: bool
    latency_ms: int | None = None
    detail: str = ""
    # When ok=true and the provider exposed a /models listing, surface a
    # trimmed sample so the editor UI can offer a model dropdown without
    # a second round-trip.
    models: list[str] = []


class ProviderModelsResponse(BaseModel):
    """Models listing for the editor's model dropdown."""
    ok: bool
    models: list[str] = []
    detail: str = ""


# ─── Configurable judge dry-run ───
#
# The editor drawer (PR-B) lets the user click "Try" on a sample
# (input, output, expected) and see exactly what the configured judge
# returns *before* saving the evaluator. ``params`` is the evaluator
# config under construction; ``provider_id`` lets the user override
# the saved provider for a one-off test (e.g. trying gpt-4o vs claude
# without committing the change).

class DryRunRequest(BaseModel):
    provider_id: str | None = None
    params: dict[str, Any] = {}
    input: str = ""
    output: str = ""
    expected_output: str | None = None
    metadata: dict[str, Any] | None = None
    # 对比模式 dry-run：mode='comparative' 时用 output_a/output_b 两份回复，
    # 走对比 judge 产出 verdict（见 DryRunResponse.verdict）。缺省单模不受影响。
    mode: str = "single"
    output_a: str = ""
    output_b: str = ""


class DryRunScoreItem(BaseModel):
    name: str
    value: float
    reason: str = ""
    # raw_value 保留模型原始输出（数值/布尔/类别名），UI 在归一分旁边
    # 展示原始值，便于核对；可能是 number / bool / string，故用 Any。
    raw_value: Any = None


class DryRunResponse(BaseModel):
    # 单分数范式：``scores`` 至多一个元素，UI 直接展示首项即可。
    # 旧的 ``aggregate`` 字段（多维度加权平均）已不复存在。
    scores: list[DryRunScoreItem] = []
    # 对比模式（mode='comparative'）dry-run 的 verdict：
    #   {dimensions:[{name,score_a,score_b,winner,reason}], overall_winner, reasoning}
    # 单分数模式为 None。
    verdict: dict[str, Any] | None = None
    model: str = ""
    usage: dict[str, int] = {}
    raw_content: str = ""
    rendered_messages: list[dict[str, str]] = []
    error: str | None = None


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
