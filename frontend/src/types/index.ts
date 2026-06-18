export interface LoginRequest {
  username: string
  password: string
}

export interface RegisterRequest {
  username: string
  email: string
  password: string
  // 入口码：非首个用户注册时必填，决定所属租户与角色（首个用户为内部超管，免码）
  entry_code?: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface User {
  id: string
  username: string
  email: string
  role: string
  is_active: boolean
  // 多租户：用户所属租户 id；内部用户挂默认内部租户（与后端 UserRow.tenant_id 对齐）
  tenant_id: string
  // 是否超级管理员（内部 admin）；为 true 时后端跨租户可见
  is_superadmin: boolean
  created_at: string
  updated_at: string
}

export interface UserUpdateRequest {
  email?: string
  password?: string
}

// 数据集类型：candidate=备选数据集（单轮，老数据默认）/ conversation=多轮对话集。
// 两类在各自页面隔离展示，互不可见。
export type DatasetType = 'candidate' | 'conversation'

export interface Dataset {
  id: string
  name: string
  description: string
  example_count: number
  created_at: string | null
  metadata: Record<string, unknown>
  dataset_type: DatasetType
}

export interface CreateDatasetRequest {
  name: string
  description?: string
  source_project?: string
  metadata?: Record<string, unknown>
  dataset_type?: DatasetType
}

export interface DatasetStats {
  total_cases: number
  by_source: Record<string, number>
  by_tag: Record<string, number>
  has_expected_output: number
  has_criteria: number
  has_tool_calls: number
  avg_messages_per_case: number
}

export interface TestCase {
  id?: string
  name: string
  description?: string
  tags?: string[]
  source?: string
  input_messages: Array<{ role: string; content: string }>
  agent_config_override?: Record<string, unknown>
  expected_output?: string
  expected_output_criteria?: string[]
  expected_tool_calls?: Array<Record<string, unknown>>
  max_tool_calls?: number
  max_latency_ms?: number
  max_tokens?: number
  scoring_mode?: string
  // 多轮对话：会话级目标 + 逐轮期望（turn_index 指向 input_messages 里 user 消息下标）
  conversation_goal?: string
  turn_expectations?: TurnExpectation[]
}

export interface TurnExpectation {
  turn_index: number
  criteria?: string[]
  expected_output?: string
}

export interface AddCasesRequest {
  cases: TestCase[]
  split?: string
}

export interface GenerateScenarioRequest {
  dataset: string
  test_scenario?: string
  case_category?: string
  count?: number
  context?: string
  dry_run?: boolean
}

export interface GenerateMutateRequest {
  dataset: string
  case_id: string
  count?: number
  strategy?: string
  target_dataset?: string
  tags?: string[]
  split?: string
  dry_run?: boolean
}

export interface ListRunsRequest {
  project_name: string
  start_time?: string
  end_time?: string
  status?: string
  tags?: string[]
  limit?: number
  page?: number
  page_size?: number
  enrich_models?: boolean
  with_io?: boolean
}

export interface PaginatedRuns {
  items: RunSummary[]
  total: number
  page: number
  page_size: number
}

export interface RunSummary {
  id: string
  name: string
  status: string
  start_time: string | null
  latency_s: number | null
  total_tokens: number | null
  error: string | null
  tags: string[]
  input_preview: string
  output_preview: string
  model_name: string
  first_token_s: number | null
  first_tool_call_s: number | null
}

export interface ExtractRequest {
  run_ids: string[]
  source?: string
  default_tags?: string[]
  include_output_as_expected?: boolean
}

export interface ImportTracesRequest {
  dataset: string
  run_ids: string[]
  project_name?: string
  source?: string
  default_tags?: string[]
  include_output_as_expected?: boolean
  split?: string
}

export interface PullDatasetRequest {
  source_dataset: string
  target_dataset?: string
  split?: string
  limit?: number
}

export interface RunChildMeta {
  id: string
  name: string
  run_type: string
  status: string
  start_time: string | null
  latency_s: number | null
  total_tokens: number | null
  error: string | null
  has_children: boolean
}

export interface RunDetail {
  id: string
  name: string
  run_type: string
  status: string
  start_time: string | null
  end_time: string | null
  latency_s: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  error: string | null
  inputs: Record<string, unknown> | null
  outputs: Record<string, unknown> | null
  extra: Record<string, unknown> | null
  metadata: Record<string, unknown> | null
  tags: string[]
  parent_run_id: string | null
  trace_id: string | null
  children: RunChildMeta[]
  children_truncated: boolean
}

export interface RunDetailRequest {
  run_id: string
  project_name?: string
}

export interface FillModelsRequest {
  project_name: string
  runs: { id: string; start_time: string | null }[]
}

export interface FillModelsResponse {
  models: Record<string, string>
  first_tool_calls: Record<string, number>
  missing: string[]
}

export interface ConfigOption {
  value: unknown
  label: string | null
}

export interface ConfigItem {
  key: string
  value: unknown                  // default option's value (back-compat)
  options: ConfigOption[]
  default_index: number
  category: string
  description: string | null
  updated_by: string | null
  updated_at: string | null
}

export interface ConfigUpdateRequest {
  value: unknown
  description?: string
}

export interface AddConfigOptionRequest {
  value: unknown
  label?: string | null
  make_default?: boolean
  description?: string
}

export interface UpdateConfigOptionRequest {
  value: unknown
  label?: string | null
}

export interface AuditLog {
  id: string
  entity_type: string
  entity_id: string
  action: string
  user_id: string | null
  details: Record<string, unknown> | null
  created_at: string
}

export interface AuditLogList {
  items: AuditLog[]
  total: number
}

export interface RoutingRule {
  id: string
  name: string
  priority: number
  source_project: string
  conditions: Record<string, unknown>
  target_dataset: string
  transform_config: Record<string, unknown>
  is_active: boolean
  created_at: string | null
  updated_at: string | null
}

export interface CreateRuleRequest {
  name: string
  priority?: number
  source_project: string
  conditions?: {
    tags?: string[]
    metadata_match?: Record<string, unknown>
    status?: string
    min_duration_ms?: number
  }
  target_dataset: string
  transform_config?: {
    include_output_as_expected?: boolean
    default_tags?: string[]
    split?: string
  }
  is_active?: boolean
}

export interface RoutingLog {
  id: string
  rule_id: string | null
  run_id: string
  source_project: string
  target_dataset: string | null
  status: string
  error_message: string | null
  created_at: string | null
}

export interface PaginatedRoutingLogs {
  items: RoutingLog[]
  total: number
  limit: number
  offset: number
}

export interface RoutingStats {
  rule_id: string | null
  total: number
  routed: number
  failed: number
  skipped: number
}

export interface SchedulerStatus {
  running: boolean
  watches: Array<{
    project_name: string
    status: string
    last_poll: string | null
  }>
}

export interface DatasetVersion {
  version_id: string
  created_at: string | null
}

export interface DuplicateInfo {
  fingerprint: string
  count: number
  example_ids: string[]
}

export interface QualityReport {
  total: number
  valid: number
  needs_review: number
  issues_by_field: Record<string, number>
  results: Array<Record<string, unknown>>
}

export interface CapacityInfo {
  dataset_name: string
  current_count: number
  max_count: number
  usage_ratio: number
  warning: boolean
}

// ─── Evaluation (Langfuse-backed) ───

export interface EvalAgentConfig {
  type: 'openai' | 'sse' | 'sse_generic'
  url: string
  api_key?: string
  model?: string
  headers?: Record<string, string>
  payload_template?: Record<string, unknown>
  timeout?: number
  language?: string
}

export interface EvaluatorConfig {
  name: string
  params?: Record<string, unknown>
}

export interface EvaluatorInstance {
  id: string
  name: string
  tag: string
  evaluator_type: string | null
  description: string | null
  params: Record<string, unknown>
  is_active: boolean
  current_version_id?: string | null
  created_at: string | null
  updated_at: string | null
}

export interface EvaluatorVersion {
  id: string
  evaluator_id: string
  version_number: number
  params: Record<string, unknown>
  description: string | null
  created_by: string | null
  created_at: string | null
}

export interface CreateEvaluatorRequest {
  name: string
  tag?: string | null
  evaluator_type?: string | null
  description?: string | null
  params?: Record<string, unknown>
  is_active?: boolean
}

export interface UpdateEvaluatorRequest {
  name?: string
  tag?: string
  description?: string | null
  params?: Record<string, unknown>
  is_active?: boolean
}

export interface UploadCasesResponse {
  source_id: string
  name: string
  count: number
  preview: Array<Record<string, unknown>>
}

export interface EvalCaseSourceSummary {
  id: string
  name: string
  source_kind: string
  file_format: string | null
  count: number
  created_at: string | null
}

export interface StartEvalRequest {
  benchmark_version_id?: string | null
  project_id?: string | null
  case_source_id?: string | null
  case_ids?: string[] | null
  filter_tags?: string[] | null
  filter_category_id?: string | null
  limit?: number | null
  agent: EvalAgentConfig
  evaluator_ids: string[]
  concurrency?: number
  run_name?: string | null
  langsmith_project?: string | null
}

export interface StartEvalResponse {
  run_id: string
  status: string
  case_count: number
}

export interface EvalRunSummary {
  id: string
  benchmark_version_id: string | null
  status: string
  started_at: string | null
  finished_at: string | null
  langfuse_run_name: string | null
  langsmith_project?: string | null
  agent_config: Record<string, unknown>
  summary_scores: {
    counts?: { total?: number; passed?: number; failed?: number; unreachable?: number }
    dimension_averages?: Record<string, number>
    score_distribution?: {
      buckets: string[]
      by_dimension: Record<string, number[]>
    }
    tool_usage?: Array<{ name: string; calls: number; errors: number; cases: number }>
    cost_success?: Record<string, number | null>
    cost_failure?: Record<string, number | null>
    retry_stats?: {
      total_cases?: number
      cases_with_retries?: number
      max_attempts?: number
      avg_attempts?: number
      total_retries?: number
    }
    langfuse_dataset?: string
    langfuse_run_name?: string
    langfuse_host?: string
    error?: string
    runtime_error?: string
    stopped_early?: boolean
  } | null
  progress: { total?: number; completed?: number; failed?: number }
  created_at: string | null
}

export interface EvalRunDetail extends EvalRunSummary {
  evaluator_configs: Array<Record<string, unknown>>
}

export interface CotStep {
  type: 'thought' | 'tool_call' | 'answer'
  content?: string
  tool_name?: string
  args?: unknown
  output?: unknown
  started_at?: number | null
  duration_ms?: number | null
  first_token_ms?: number | null
}

export interface EvalResultRow {
  id: string
  benchmark_case_id: string | null
  test_case_id: string | null
  status: string
  actual_output: string | null
  question?: string | null
  latency_ms: number | null
  total_tokens: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  cache_creation_tokens?: number | null
  cache_read_tokens?: number | null
  tool_call_count: number | null
  first_thinking_token_ms?: number | null
  first_answer_token_ms?: number | null
  actual_tool_calls?: Array<Record<string, unknown>> | null
  full_trace?: { steps?: CotStep[] } | null
  error_message: string | null
  langfuse_trace_id: string | null
  langsmith_run_id?: string | null
  attempts_made?: number
  scores: Record<string, number>
}

export interface EvalResultsPage {
  items: EvalResultRow[]
  total: number
  page: number
  page_size: number
}

export interface EvalRunsPage {
  items: EvalRunSummary[]
  total: number
  page: number
  page_size: number
}

export interface BuiltinEvaluator {
  name: string
  description: string
  params_schema: Record<string, unknown>
}

// ─── Evaluator Providers (LLM-judge endpoints) ───

export type ProviderType =
  | 'openai'
  | 'openai_compatible'
  | 'anthropic'
  | 'deepseek'
  | 'azure'
  | 'custom'

export interface EvaluatorProvider {
  id: string
  name: string
  provider_type: string
  base_url: string | null
  default_model: string | null
  extra_config: Record<string, unknown>
  is_active: boolean
  has_api_key: boolean
  api_key_masked: string
  created_at: string | null
  updated_at: string | null
}

export interface CreateEvaluatorProviderRequest {
  name: string
  provider_type: string
  base_url?: string | null
  api_key?: string | null
  default_model?: string | null
  extra_config?: Record<string, unknown>
  is_active?: boolean
}

export interface UpdateEvaluatorProviderRequest {
  name?: string
  provider_type?: string
  base_url?: string | null
  // omit: keep existing; "": clear; non-empty: replace
  api_key?: string | null
  default_model?: string | null
  extra_config?: Record<string, unknown>
  is_active?: boolean
}

export interface TestProviderResponse {
  ok: boolean
  latency_ms: number | null
  detail: string
  models: string[]
}

export interface ProviderModelsResponse {
  ok: boolean
  models: string[]
  detail: string
}

// ─── Configurable judge dry-run ───

export interface DryRunRequest {
  provider_id?: string | null
  params: Record<string, unknown>
  input: string
  output: string
  expected_output?: string | null
  metadata?: Record<string, unknown> | null
}

export interface DryRunScoreItem {
  name: string
  value: number
  reason: string
  // 模型原始输出（数值/布尔/类别名），UI 在归一分旁展示便于核对
  raw_value?: number | boolean | string | null
}

export interface DryRunResponse {
  // 单分数范式：scores 至多一个元素
  scores: DryRunScoreItem[]
  model: string
  usage: Record<string, number>
  raw_content: string
  rendered_messages: Array<{ role: string; content: string }>
  error: string | null
}

export interface RequestLogEntry {
  timestamp: string
  method: string
  path: string
  status: number
  latency_ms: number
  request_id: string
  query: string
  client: string
  error: string | null
  body_preview: string | null
  body_truncated: boolean
}

export interface RequestLogResponse {
  capacity: number
  returned: number
  entries: RequestLogEntry[]
}
