import api from './client'

// ──────────────────────────────────────────────────────────────────────────
// Langfuse 指标 service（admin 专属）。后端字段已是 snake_case，UI 直接透传，
// 仅做浅层类型声明 + 默认值兜底。每个方法返回 { data } 形状，与项目其他 service
// 一致（页面侧 .then((r) => r.data) 消费）。baseURL 已含 /api，故路径不带前缀。
// ──────────────────────────────────────────────────────────────────────────

/** GET /langfuse-metrics/stats —— 总览 KPI。 */
export interface LangfuseStats {
  total_traces: number
  avg_latency_s: number | null
  avg_total_tokens: number | null
  total_tokens_sum: number | null
  total_cost: number | null
  avg_first_tool_call_s: number | null
  avg_first_thinking_token_s: number | null
  avg_first_answer_token_s: number | null
  tool_calls_sum: number | null
  tool_success_sum: number | null
  overall_tool_success_rate: number | null // 0-1
  error_trace_count: number | null
  cache_hit_rate: number | null // 后端恒 null
  environments: string[]
}

/** GET /langfuse-metrics/trends 的单个时间桶。 */
export interface LangfuseTrendBucket {
  date: string
  trace_count: number
  avg_latency_s: number | null
  total_cost: number | null
  total_tokens: number | null
  tool_success_rate: number | null // 0-1
  // 错误趋势 + 首 token 时间趋势
  error_count: number
  avg_first_tool_call_s: number | null
  avg_first_thinking_token_s: number | null
  avg_first_answer_token_s: number | null
}

export interface LangfuseTrends {
  buckets: LangfuseTrendBucket[]
}

/** GET /langfuse-metrics/traces 列表行。 */
export interface LangfuseTraceRow {
  langfuse_trace_id: string
  name: string | null
  environment: string | null
  trace_timestamp: string
  latency_s: number | null
  total_tokens: number | null
  total_cost: number | null
  tool_call_count: number | null
  tool_success_rate: number | null // 0-1
  has_error: boolean
  input_preview: string | null // 后端截断后的 input 文本预览
}

export interface LangfuseTracesPage {
  total: number
  page: number
  page_size: number
  traces: LangfuseTraceRow[]
}

/** 单条 observation 明细。 */
export interface LangfuseObservation {
  id: string
  type: string | null
  name: string | null
  level: string | null
  status_message: string | null
  model: string | null
  start_time: string | null
  latency_s: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  calculated_total_cost: number | null
  time_to_first_token_s: number | null
}

/** GET /langfuse-metrics/traces/{id} —— trace 全字段 + observations。 */
export interface LangfuseTraceDetail {
  langfuse_trace_id: string
  name: string | null
  environment: string | null
  trace_timestamp: string
  latency_s: number | null
  total_tokens: number | null
  total_cost: number | null
  tool_call_count: number | null
  tool_success_count: number | null
  tool_success_rate: number | null
  first_tool_call_s: number | null
  first_thinking_token_s: number | null
  first_answer_token_s: number | null
  has_error: boolean
  // Langfuse 透传的 input/output：可能是字符串，也可能是 JSON 对象/数组
  // （LangGraph 等会塞 messages 结构）。页面侧 toDisplayText 负责归一化展示。
  input: unknown
  output: unknown
  cache_hit_rate: number | null // 恒 null
  observations: LangfuseObservation[]
  [key: string]: unknown // trace 全字段透传，容忍后端额外字段
}

/** GET /langfuse-metrics/poll/status —— 轮询健康状态。 */
export interface LangfusePollStatus {
  status: string
  last_polled_at: string | null
  consecutive_failures: number
  last_error: string | null
  [key: string]: unknown
}

/** POST /langfuse-metrics/poll —— 手动触发一次轮询。 */
export interface LangfusePollResult {
  status: string
  last_run_traces: number
  last_run_observations: number
}

export interface LangfuseQueryParams {
  environment?: string
  from?: string
  to?: string
}

export interface LangfuseTracesParams extends LangfuseQueryParams {
  name?: string
  has_error?: boolean
  page?: number
  page_size?: number
}

export const langfuseMetricsApi = {
  /** 总览 KPI + 可用 environment 列表。 */
  stats(params: LangfuseQueryParams = {}) {
    return api
      .get<LangfuseStats>('/langfuse-metrics/stats', { params })
      .then((r) => ({
        data: {
          ...r.data,
          environments: r.data.environments ?? [],
        } as LangfuseStats,
      }))
  },
  /** 时间序列趋势（按天分桶）。 */
  trends(params: LangfuseQueryParams & { bucket?: string } = {}) {
    return api
      .get<LangfuseTrends>('/langfuse-metrics/trends', {
        params: { bucket: 'day', ...params },
      })
      .then((r) => ({ data: { buckets: r.data.buckets ?? [] } as LangfuseTrends }))
  },
  /** 分页 trace 列表。 */
  traces(params: LangfuseTracesParams = {}) {
    return api
      .get<LangfuseTracesPage>('/langfuse-metrics/traces', { params })
      .then((r) => ({
        data: {
          total: r.data.total,
          page: r.data.page,
          page_size: r.data.page_size,
          traces: r.data.traces ?? [],
        } as LangfuseTracesPage,
      }))
  },
  /** 单 trace 全字段 + observations 明细。 */
  trace(id: string) {
    return api
      .get<LangfuseTraceDetail>(`/langfuse-metrics/traces/${id}`)
      .then((r) => ({
        data: {
          ...r.data,
          observations: r.data.observations ?? [],
        } as LangfuseTraceDetail,
      }))
  },
  /** 手动触发一次轮询。 */
  poll() {
    return api
      .post<LangfusePollResult>('/langfuse-metrics/poll')
      .then((r) => ({ data: r.data }))
  },
  /** 轮询健康状态。 */
  pollStatus() {
    return api
      .get<LangfusePollStatus>('/langfuse-metrics/poll/status')
      .then((r) => ({ data: r.data }))
  },
}
