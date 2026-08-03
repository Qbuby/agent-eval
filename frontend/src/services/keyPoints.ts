import api from './client'

// 从参考答案提炼关键点：批量走异步 job（内存态，进度靠轮询），
// 编辑弹窗里的单条提炼走同步端点，结果只回填表单不落库。
// target 与回写位置对应：
//   candidate  → candidate_cases.key_points（源 answer）
//   benchmark  → benchmark_cases.key_points（源 reference_answer）
//   multichat  → Langfuse item 的 expected_output.turn_expectations[].criteria
export type KeyPointsTarget = 'candidate' | 'benchmark' | 'multichat'

export type KeyPointsPhase =
  | 'pending'
  | 'collecting'
  | 'extracting'
  | 'writing'
  | 'done'
  | 'failed'
  | 'cancelled'

export interface ExtractKeyPointsRequest {
  target: KeyPointsTarget
  // 不传则按 target 全量扫「有答案且关键点为空」的样例
  case_ids?: string[]
  // multichat 必填：Langfuse 数据集名
  dataset_name?: string
  limit?: number
  provider_name?: string
  model?: string
  concurrency?: number
}

export interface KeyPointsJobStatus {
  job_id: string
  phase: KeyPointsPhase
  total: number
  done: number
  extracted: number
  failed: number
  written: number
  skipped_short: number
  error: string | null
  targets: string[]
  active: boolean
}

export interface PendingCount {
  target: KeyPointsTarget
  pending: number
  // 答案过短、提炼没意义而被跳过的条数
  skipped_short: number
}

export interface ExtractOneRequest {
  answer: string
  question?: string
  provider_name?: string
  model?: string
}

const TERMINAL_PHASES: KeyPointsPhase[] = ['done', 'failed', 'cancelled']

export function isTerminalPhase(phase: KeyPointsPhase) {
  return TERMINAL_PHASES.includes(phase)
}

export const keyPointsApi = {
  // ── 批量提炼 ──
  extract(data: ExtractKeyPointsRequest) {
    return api.post<{ job_id: string; phase: KeyPointsPhase }>('/key-points/extract', data)
  },
  getJob(jobId: string) {
    return api.get<KeyPointsJobStatus>(`/key-points/jobs/${jobId}`)
  },
  cancelJob(jobId: string) {
    return api.post<{ job_id: string; cancelled: boolean }>(`/key-points/jobs/${jobId}/cancel`)
  },
  // 点按钮前先问一下会提炼多少条，避免用户对着 0 条空跑
  pendingCount(params: { target: KeyPointsTarget; dataset_name?: string; limit?: number }) {
    return api.get<PendingCount>('/key-points/pending-count', { params })
  },

  // ── 单条同步提炼（编辑弹窗）──
  extractOne(data: ExtractOneRequest) {
    return api.post<{ points: string[] }>('/key-points/extract-one', data)
  },
}
