import api from './client'
import type { EvalAgentConfig } from '@/types'

// 持久化 agent 回复：三类数据集（备选 / 基准 / 多轮对话）的样例都能先让 agent
// 跑出答案落成版本，之后评估可直接消费这些回复而不再实时连 agent。
// 样例引用统一是 (dataset_type, case_ref)：
//   candidate / benchmark → 本地表主键字符串
//   conversation          → Langfuse dataset item id
export type ReplyDatasetType = 'candidate' | 'benchmark' | 'conversation'

export interface GenerateRepliesRequest {
  dataset_type: ReplyDatasetType
  dataset_name?: string | null
  project_id?: string | null
  case_ids: string[]
  agent: EvalAgentConfig
  version_label?: string | null
  concurrency?: number
}

export interface GenerateRepliesResponse {
  job_id: string
  status: string
  case_count: number
}

export interface ReplyVersion {
  id: string
  dataset_type: ReplyDatasetType
  case_ref: string
  version_number: number
  version_label: string | null
  content: string | null
  turns: Array<Record<string, unknown>> | null
  status: string
  error_message: string | null
  latency_ms: number | null
  total_tokens: number | null
  edited: boolean
  is_current: boolean
  agent_config: Record<string, unknown>
  created_by: string | null
  created_by_name: string | null
  created_at: string | null
  updated_at: string | null
  used_by_results: number
}

export interface CaseReplyState {
  case_ref: string
  has_reply: boolean
  current_version_id: string | null
  current_version_number: number | null
  current_version_label: string | null
  version_count: number
}

export interface ReplyJobItem {
  id: string
  case_ref: string
  question: string | null
  status: string
  error_message: string | null
  version_id: string | null
}

export interface ReplyJob {
  id: string
  dataset_type: ReplyDatasetType
  dataset_name: string | null
  status: string
  version_label: string | null
  total_count: number
  succeeded_count: number
  failed_count: number
  running_count: number
  cancel_requested: boolean
  created_at: string | null
  finished_at: string | null
  created_by: string | null
  created_by_name: string | null
  items: ReplyJobItem[]
}

// ── 批量切换当前版本 ──
// version_id 是 per-(dataset_type, case_ref) 的，跨样例不可复用，所以批量切换只
// 能按跨样例可识别的标识来指定：最新 / 精确版本号 vN / 版本备注。
export type BatchVersionMode = 'latest' | 'version_number' | 'label'

export interface BatchVersionSelector {
  mode: BatchVersionMode
  version_number?: number | null
  label?: string | null
}

export interface BatchSetCurrentRequest {
  dataset_type: ReplyDatasetType
  case_refs: string[]
  selector: BatchVersionSelector
}

export interface BatchResolveItem {
  case_ref: string
  matched: boolean
  already_current: boolean
  version_id: string | null
  version_number: number | null
  version_label: string | null
  status: string | null
  reason: string | null
}

export interface BatchOption {
  value: string
  case_count: number
}

export interface BatchResolveResult {
  total: number
  matched_count: number
  changed_count: number
  unchanged_count: number
  missing_count: number
  items: BatchResolveItem[]
  label_options: BatchOption[]
  version_number_options: BatchOption[]
}

export interface BatchSetCurrentResult {
  total: number
  changed_count: number
  unchanged_count: number
  missing_count: number
  failed_count: number
  items: BatchResolveItem[]
}

export const agentRepliesApi = {
  // ── 生成任务 ──
  generate(data: GenerateRepliesRequest) {
    return api.post<GenerateRepliesResponse>('/agent-replies/generate', data)
  },
  // 单条重试：语义等同 generate，只提交一个样例（独立端点便于排查）。
  retryCase(data: GenerateRepliesRequest) {
    return api.post<GenerateRepliesResponse>('/agent-replies/retry-case', data)
  },
  listJobs(params?: {
    dataset_type?: ReplyDatasetType
    dataset_name?: string
    project_id?: string
    active_only?: boolean
    limit?: number
  }) {
    return api.get<ReplyJob[]>('/agent-replies/jobs', { params })
  },
  getJob(jobId: string) {
    return api.get<ReplyJob>(`/agent-replies/jobs/${jobId}`)
  },
  cancelJob(jobId: string) {
    return api.post<{ job_id: string; status: string; cancelled: boolean }>(
      `/agent-replies/jobs/${jobId}/cancel`,
    )
  },
  retryFailed(jobId: string) {
    return api.post<GenerateRepliesResponse>(`/agent-replies/jobs/${jobId}/retry-failed`)
  },

  // ── 版本管理 ──
  // 列表页给每行打「已生成 / N 个版本」标记：一次批量查。
  listStates(datasetType: ReplyDatasetType, caseRefs: string[]) {
    return api.get<CaseReplyState[]>('/agent-replies/states', {
      params: { dataset_type: datasetType, case_refs: caseRefs.join(',') },
    })
  },
  listVersions(datasetType: ReplyDatasetType, caseRef: string) {
    return api.get<ReplyVersion[]>('/agent-replies/versions', {
      params: { dataset_type: datasetType, case_ref: caseRef },
    })
  },
  updateVersion(
    versionId: string,
    data: {
      content?: string
      version_label?: string
      turns?: Array<Record<string, unknown>>
    },
  ) {
    return api.patch<ReplyVersion>(`/agent-replies/versions/${versionId}`, data)
  },
  setCurrentVersion(versionId: string) {
    return api.post<ReplyVersion>(`/agent-replies/versions/${versionId}/set-current`)
  },
  // 干跑预览：这批样例按这个标识各自会切到哪个版本，谁切不了、为什么。
  // 顺带返回可选的版本备注 / 版本号下拉（含各自命中样例数）。
  batchResolve(data: BatchSetCurrentRequest) {
    return api.post<BatchResolveResult>('/agent-replies/batch-resolve', data)
  },
  // 执行：部分成功即提交，解析不到的样例原样不动。
  batchSetCurrent(data: BatchSetCurrentRequest) {
    return api.post<BatchSetCurrentResult>('/agent-replies/batch-set-current', data)
  },
  deleteVersion(versionId: string) {
    return api.delete<{
      deleted: boolean
      remaining_count: number
      current_version_id: string | null
    }>(`/agent-replies/versions/${versionId}`)
  },
}
