import api from './client'
import type {
  BuiltinEvaluator,
  CreateEvaluatorRequest,
  EvalCaseSourceSummary,
  EvalResultsPage,
  EvalRunDetail,
  EvalRunsPage,
  EvaluatorInstance,
  RunDetail,
  StartEvalRequest,
  StartEvalResponse,
  UpdateEvaluatorRequest,
  UploadCasesResponse,
} from '@/types'

export const evaluationApi = {
  // ── builtin templates ──
  listBuiltinEvaluators() {
    return api.get<BuiltinEvaluator[]>('/eval/evaluators/builtin')
  },

  // ── evaluator instances (named, reusable) ──
  listEvaluators(activeOnly?: boolean) {
    return api.get<EvaluatorInstance[]>('/eval/evaluators', {
      params: activeOnly ? { active_only: true } : {},
    })
  },
  createEvaluator(data: CreateEvaluatorRequest) {
    return api.post<EvaluatorInstance>('/eval/evaluators', data)
  },
  updateEvaluator(id: string, data: UpdateEvaluatorRequest) {
    return api.put<EvaluatorInstance>(`/eval/evaluators/${id}`, data)
  },
  deleteEvaluator(id: string) {
    return api.delete<{ id: string; deleted: boolean }>(`/eval/evaluators/${id}`)
  },

  // ── case file upload ──
  uploadCases(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    return api.post<UploadCasesResponse>('/eval/case_sources/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  listCaseSources() {
    return api.get<EvalCaseSourceSummary[]>('/eval/case_sources')
  },
  getCaseSource(id: string) {
    return api.get<{
      id: string
      name: string
      file_format: string | null
      cases: Array<Record<string, unknown>>
      created_at: string | null
    }>(`/eval/case_sources/${id}`)
  },

  // ── runs ──
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
  getResultTrace(resultId: string) {
    return api.get<RunDetail>(`/eval/results/${resultId}/trace`)
  },
  stopRun(runId: string) {
    return api.post<{ run_id: string; status: string }>(`/eval/runs/${runId}/stop`)
  },
}
