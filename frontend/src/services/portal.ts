import api from './client'

// Portal（外部客户）数据层。对接 routers/portal.py 的端点（见共享设计 §6.2）。
// 端点细节未在后端最终敲定处，按 RESTful 合理假设；若后端返回字段名不同，
// 调整此处的类型与路径即可（仅本文件 + pages/portal/* 受影响）。

/** 一次 xlsx 上传对应一个批次。 */
export interface PortalBatch {
  id: string
  name: string
  row_count: number
  status: string
  created_at: string
}

/** 客户对单条样例提交的打分 + 意见。 */
export interface SampleFeedback {
  id?: string
  overall: number | null
  // 维度分：relevance / difficulty / answer_accuracy，1-5
  scores: Record<string, number>
  comment: string | null
  updated_at?: string
}

/** 单条样例，含本人已提交的反馈（若有）。 */
export interface PortalSample {
  id: string
  row_index: number
  question: string
  answer: string | null
  // xlsx 其余列原样透出，渲染为附加字段
  extra?: Record<string, unknown> | null
  feedback?: SampleFeedback | null
}

export interface PaginatedSamples {
  items: PortalSample[]
  total: number
  page: number
  page_size: number
}

export interface FeedbackPayload {
  overall: number | null
  scores: Record<string, number>
  comment: string | null
}

export const portalApi = {
  /** 上传 xlsx，后端解析落库后返回批次概要。 */
  uploadBatch(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    return api.post<PortalBatch>('/portal/batches/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  /** 当前租户的批次列表。 */
  listBatches() {
    return api.get<PortalBatch[]>('/portal/batches')
  },
  /** 分页拉取某批次的样例（含本人 feedback）。 */
  listSamples(batchId: string, params?: { page?: number; page_size?: number }) {
    return api.get<PaginatedSamples>(`/portal/batches/${batchId}/samples`, { params })
  },
  /** 提交/更新单条样例的打分 + 意见（按 sample_id + 本人 upsert）。 */
  submitFeedback(sampleId: string, data: FeedbackPayload) {
    return api.post<SampleFeedback>(`/portal/samples/${sampleId}/feedback`, data)
  },
  /** 删除整个样例集（批次）；后端 FK 级联删除其样例与反馈。 */
  deleteBatch(batchId: string) {
    return api.delete<void>(`/portal/batches/${batchId}`)
  },
}

// 打分维度定义：UI 渲染维度分输入用。key 与后端 scores JSON 对齐。
export const SCORE_DIMENSIONS: { key: string; label: string }[] = [
  { key: 'relevance', label: '相关性' },
  { key: 'difficulty', label: '难度' },
  { key: 'answer_accuracy', label: '答案准确性' },
]
