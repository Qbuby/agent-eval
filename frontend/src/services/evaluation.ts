import api from './client'
import type {
  BuiltinEvaluator,
  EvalResultsPage,
  EvalRunDetail,
  EvalRunsPage,
  StartEvalRequest,
  StartEvalResponse,
} from '@/types'

export const evaluationApi = {
  listBuiltinEvaluators() {
    return api.get<BuiltinEvaluator[]>('/eval/evaluators/builtin')
  },
  startRun(data: StartEvalRequest) {
    return api.post<StartEvalResponse>('/eval/runs/start', data)
  },
  listRuns(params?: {
    benchmark_version_id?: string
    status?: string
    page?: number
    page_size?: number
  }) {
    return api.get<EvalRunsPage>('/eval/runs', { params })
  },
  getRun(runId: string) {
    return api.get<EvalRunDetail>(`/eval/runs/${runId}`)
  },
  getResults(runId: string, params?: { page?: number; page_size?: number }) {
    return api.get<EvalResultsPage>(`/eval/runs/${runId}/results`, { params })
  },
  stopRun(runId: string) {
    return api.post<{ run_id: string; status: string }>(`/eval/runs/${runId}/stop`)
  },
}
