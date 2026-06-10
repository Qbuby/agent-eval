import api from './client'

// ---- 类型契约（与 feedback-api 摊 routers/feedback_review.py §6.3 对齐）----
// 这些 shape 是本 UI 摊预先约定的响应结构。feedback-api 摊请据此返回字段，
// 如需调整请同步修改本文件。

/** GET /api/feedback/batches 的单条批次（含聚合）。 */
export interface FeedbackBatchSummary {
  batch_id: string
  batch_name: string
  tenant_id: string
  tenant_name: string
  row_count: number // 批次样例总数
  rated_count: number // 已有至少一条反馈的样例数
  feedback_count: number // 反馈条数（一个样例可被多人评）
  avg_overall: number | null // 平均总体分（1-5），无反馈为 null
  created_at: string
}

export interface FeedbackBatchesResponse {
  batches: FeedbackBatchSummary[]
}

/** 单条客户反馈明细。 */
export interface SampleFeedbackDetail {
  id: string
  rated_by: string | null
  rated_by_name: string | null // 评价人用户名（后端 join users）
  overall: number | null
  scores: Record<string, number> // 维度→分
  comment: string | null
  created_at: string
  updated_at: string
}

/** GET /api/feedback/batches/{id}/samples 列表行（轻量，不含全部反馈明细）。 */
export interface FeedbackSampleRow {
  id: string
  row_index: number
  question: string
  answer: string | null
  feedback_count: number
  avg_overall: number | null
}

export interface FeedbackBatchSamplesResponse {
  samples: FeedbackSampleRow[]
  total: number
  page: number
  page_size: number
  batch_name: string
  tenant_name: string
}

/** GET /api/feedback/samples/{id} 单样例 + 其所有反馈。 */
export interface FeedbackSampleDetail {
  id: string
  batch_id: string
  batch_name: string
  tenant_id: string
  tenant_name: string
  row_index: number
  question: string
  answer: string | null
  extra: Record<string, unknown>
  feedbacks: SampleFeedbackDetail[]
}

/** GET /api/feedback/stats 总览中的按批次聚合行。 */
export interface FeedbackStatRow {
  tenant_id: string
  tenant_name: string
  batch_id: string
  batch_name: string
  row_count: number
  rated_count: number
  coverage: number // rated_count / row_count，0-1
  avg_overall: number | null
}

export interface FeedbackStatsResponse {
  rows: FeedbackStatRow[]
  // 全局汇总
  total_batches: number
  total_samples: number
  total_rated: number
  total_feedbacks: number
  overall_avg: number | null
}

export const feedbackReviewApi = {
  /** 跨租户列出有反馈的批次 + 聚合。tenant_id 可选过滤。 */
  batches(params: { tenant_id?: string } = {}) {
    return api.get<FeedbackBatchesResponse>('/feedback/batches', { params })
  },
  /** 某批次下的分页样例（含每条样例的反馈聚合）。
   * 注意：§6.3 未列此端点，本 UI 需要它从批次下钻到样例。详见 notes。 */
  batchSamples(batchId: string, params: { page?: number; page_size?: number } = {}) {
    return api.get<FeedbackBatchSamplesResponse>(`/feedback/batches/${batchId}/samples`, { params })
  },
  /** 单样例 + 其所有客户反馈明细。 */
  sample(sampleId: string) {
    return api.get<FeedbackSampleDetail>(`/feedback/samples/${sampleId}`)
  },
  /** 总览：按租户/批次聚合平均分、覆盖率。 */
  stats() {
    return api.get<FeedbackStatsResponse>('/feedback/stats')
  },
}
