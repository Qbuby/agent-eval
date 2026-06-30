import api from './client'

// ---- 类型契约 ----
// 后端 routers/feedback_review.py 的响应字段名与本 UI 的历史命名不一致
// （name vs batch_name、sample_count vs row_count、by_tenant vs rows 等）。
// 为避免改动多个页面组件，统一在本 service 层把后端字段映射成 UI 期望的形状。
// UI 侧消费的类型定义（下方 interface）即映射后的形状。

/** GET /api/feedback/batches 的单条批次（含聚合）—— UI 形状。 */
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

/** 单条客户反馈明细 —— UI 形状。 */
export interface SampleFeedbackDetail {
  id: string
  rated_by: string | null
  rated_by_name: string | null // 评价人用户名（后端 rated_by_username）
  overall: number | null
  scores: Record<string, number> // 维度→分
  comment: string | null
  expected_answer: string | null // 评审人补写的期望答案（参考标准答案）
  created_at: string
  updated_at: string
}

/** GET /api/feedback/batches/{id}/samples 列表行 —— UI 形状。 */
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

/** GET /api/feedback/samples/{id} 单样例 + 其所有反馈 —— UI 形状。 */
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

/** GET /api/feedback/stats 总览中的按租户聚合行 —— UI 形状。 */
export interface FeedbackStatRow {
  tenant_id: string
  tenant_name: string
  batch_count: number
  sample_count: number
  rated_count: number
  coverage: number // rated / sample，0-1
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

// ---- 后端原始响应形状（仅本文件内部使用）----
interface RawBatch {
  batch_id: string
  name: string
  tenant_id: string
  tenant_name: string | null
  status: string
  created_at: string
  sample_count: number
  rated_sample_count: number
  feedback_count: number
  avg_overall: number | null
  coverage: number
}
interface RawBatchList {
  batches: RawBatch[]
}
interface RawSampleRow {
  id: string
  row_index: number
  question: string
  answer: string | null
  feedback_count: number
  avg_overall: number | null
}
interface RawBatchSamples {
  batch_id: string
  batch_name: string | null
  tenant_id: string
  tenant_name: string | null
  total: number
  page: number
  page_size: number
  samples: RawSampleRow[]
}
interface RawFeedback {
  id: string
  rated_by: string | null
  rated_by_username: string | null
  overall: number | null
  scores: Record<string, number>
  comment: string | null
  expected_answer: string | null
  created_at: string
  updated_at: string
}
interface RawSampleDetail {
  sample_id: string
  batch_id: string
  batch_name: string | null
  tenant_id: string
  tenant_name: string | null
  row_index: number
  question: string
  answer: string | null
  extra: Record<string, unknown>
  feedback_count: number
  avg_overall: number | null
  feedbacks: RawFeedback[]
}
interface RawTenantStat {
  tenant_id: string
  tenant_name: string | null
  batch_count: number
  sample_count: number
  rated_sample_count: number
  feedback_count: number
  avg_overall: number | null
  coverage: number
}
interface RawStats {
  total_tenants: number
  total_batches: number
  total_samples: number
  total_rated_samples: number
  total_feedbacks: number
  avg_overall: number | null
  coverage: number
  by_tenant: RawTenantStat[]
}

// ---- 映射函数 ----
function mapBatch(b: RawBatch): FeedbackBatchSummary {
  return {
    batch_id: b.batch_id,
    batch_name: b.name,
    tenant_id: b.tenant_id,
    tenant_name: b.tenant_name ?? '—',
    row_count: b.sample_count,
    rated_count: b.rated_sample_count,
    feedback_count: b.feedback_count,
    avg_overall: b.avg_overall,
    created_at: b.created_at,
  }
}

function mapFeedback(f: RawFeedback): SampleFeedbackDetail {
  return {
    id: f.id,
    rated_by: f.rated_by,
    rated_by_name: f.rated_by_username,
    overall: f.overall,
    scores: f.scores ?? {},
    comment: f.comment,
    expected_answer: f.expected_answer,
    created_at: f.created_at,
    updated_at: f.updated_at,
  }
}

export const feedbackReviewApi = {
  /** 跨租户列出有反馈的批次 + 聚合。tenant_id 可选过滤。 */
  batches(params: { tenant_id?: string } = {}) {
    return api
      .get<RawBatchList>('/feedback/batches', { params })
      .then((r) => ({ data: { batches: (r.data.batches ?? []).map(mapBatch) } }))
  },
  /** 某批次下的分页样例（含每条样例的反馈聚合）。 */
  batchSamples(batchId: string, params: { page?: number; page_size?: number } = {}) {
    return api
      .get<RawBatchSamples>(`/feedback/batches/${batchId}/samples`, { params })
      .then((r) => ({
        data: {
          samples: r.data.samples ?? [],
          total: r.data.total,
          page: r.data.page,
          page_size: r.data.page_size,
          batch_name: r.data.batch_name ?? '',
          tenant_name: r.data.tenant_name ?? '',
        } as FeedbackBatchSamplesResponse,
      }))
  },
  /** 单样例 + 其所有客户反馈明细。 */
  sample(sampleId: string) {
    return api.get<RawSampleDetail>(`/feedback/samples/${sampleId}`).then((r) => ({
      data: {
        id: r.data.sample_id,
        batch_id: r.data.batch_id,
        batch_name: r.data.batch_name ?? '',
        tenant_id: r.data.tenant_id,
        tenant_name: r.data.tenant_name ?? '—',
        row_index: r.data.row_index,
        question: r.data.question,
        answer: r.data.answer,
        extra: r.data.extra ?? {},
        feedbacks: (r.data.feedbacks ?? []).map(mapFeedback),
      } as FeedbackSampleDetail,
    }))
  },
  /** 总览：按租户聚合平均分、覆盖率。 */
  stats() {
    return api.get<RawStats>('/feedback/stats').then((r) => ({
      data: {
        rows: (r.data.by_tenant ?? []).map(
          (t): FeedbackStatRow => ({
            tenant_id: t.tenant_id,
            tenant_name: t.tenant_name ?? '—',
            batch_count: t.batch_count,
            sample_count: t.sample_count,
            rated_count: t.rated_sample_count,
            coverage: t.coverage,
            avg_overall: t.avg_overall,
          }),
        ),
        total_batches: r.data.total_batches,
        total_samples: r.data.total_samples,
        total_rated: r.data.total_rated_samples,
        total_feedbacks: r.data.total_feedbacks,
        overall_avg: r.data.avg_overall,
      } as FeedbackStatsResponse,
    }))
  },
}
