import api from './client'
import { triggerExport, type ExportFormat } from '@/lib/download'
import type {
  BuiltinEvaluator,
  CreateEvaluatorRequest,
  DryRunRequest,
  DryRunResponse,
  EvalCaseSourceSummary,
  EvalResultsPage,
  EvalRunDetail,
  EvalRunsPage,
  EvaluatorInstance,
  EvaluatorVersion,
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
  dryRunEvaluator(id: string, data: DryRunRequest) {
    return api.post<DryRunResponse>(`/eval/evaluators/${id}/dry-run`, data)
  },
  listEvaluatorVersions(id: string) {
    return api.get<EvaluatorVersion[]>(`/eval/evaluators/${id}/versions`)
  },
  createEvaluatorVersion(
    id: string,
    data: { params: Record<string, unknown>; description?: string | null; activate?: boolean },
  ) {
    return api.post<EvaluatorVersion>(`/eval/evaluators/${id}/versions`, data)
  },
  activateEvaluatorVersion(id: string, versionId: string) {
    return api.post<EvaluatorInstance>(
      `/eval/evaluators/${id}/versions/${versionId}/activate`,
    )
  },

  // ── import / export（跨环境搬运那 6 个多轮 + 3 个单轮 configurable_judge）──
  // 导出走 blob 下载：后端把 provider_id 换成 provider name，不含密钥。
  exportEvaluators() {
    return triggerExport({
      url: '/eval/evaluators/export',
      format: 'json',
      fallbackName: 'evaluators-export',
    })
  },
  // 导入回填 provider name→本地 provider_id；同名更新（追加版本），
  // provider 缺失则跳过。返回 created/updated/skipped 摘要。
  importEvaluators(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    return api.post<{
      created: string[]
      updated: string[]
      skipped: Array<{ name: string; reason: string }>
    }>('/eval/evaluators/import', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
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
    started_after?: string  // ISO timestamp
    started_before?: string
    q?: string  // text search over run name / agent model+url / project
    min_pass_rate?: number  // 0..1
    include_deleted?: boolean
    page?: number
    page_size?: number
  }) {
    return api.get<EvalRunsPage>('/eval/runs', { params })
  },
  deleteRun(runId: string) {
    return api.delete<{ run_id: string; deleted: boolean }>(`/eval/runs/${runId}`)
  },
  getRun(runId: string) {
    return api.get<EvalRunDetail>(`/eval/runs/${runId}`)
  },
  getResults(runId: string, params?: { page?: number; page_size?: number }) {
    return api.get<EvalResultsPage>(`/eval/runs/${runId}/results`, { params })
  },
  async getAllResults(runId: string): Promise<EvalResultsPage> {
    const pageSize = 200
    const first = (await api.get<EvalResultsPage>(`/eval/runs/${runId}/results`, {
      params: { page: 1, page_size: pageSize },
    })).data
    const items = [...first.items]
    const pageCount = Math.ceil(first.total / pageSize)
    for (let page = 2; page <= pageCount; page += 1) {
      const next = (await api.get<EvalResultsPage>(`/eval/runs/${runId}/results`, {
        params: { page, page_size: pageSize },
      })).data
      items.push(...next.items)
    }
    return { ...first, items, page: 1, page_size: items.length }
  },
  exportResults(runId: string, format: ExportFormat) {
    return triggerExport({
      url: `/eval/runs/${runId}/results/export`,
      params: { format },
      format,
      fallbackName: `eval_run_${runId.slice(0, 8)}_results`,
    })
  },
  exportCompare(runIds: string[], format: ExportFormat, alignKey: 'case_id' | 'question' = 'case_id') {
    return triggerExport({
      method: 'post',
      url: '/eval/runs/export-compare',
      data: { run_ids: runIds, align_key: alignKey, format },
      format,
      fallbackName: 'eval_compare',
    })
  },

  // 单次评估 LLM 解读（markdown）。后端 LLM 不可用时降级规则摘要，仍返回字符串。
  getRunReport(runId: string) {
    return api.get<{ run_id: string; run_name: string; report: string }>(
      `/eval/runs/${runId}/report`,
    )
  },

  // 多 run 对比 LLM 解读（markdown）。alignKeys 传入时按当前勾选子集重算。
  getCompareReport(runIds: string[], alignKey = 'case_id', alignedKeys?: string[]) {
    return api.post<{
      run_ids: string[]
      report: string
      scope: 'full_runs' | 'selected_subset'
    }>(
      `/eval/runs/compare-report`,
      { run_ids: runIds, align_key: alignKey, aligned_keys: alignedKeys },
    )
  },
  // Batch-export per-sample results for the selected runs (same columns as the
  // single-run detail export, concatenated across runs).
  exportRunsSummary(runIds: string[], format: ExportFormat) {
    return triggerExport({
      method: 'post',
      url: '/eval/runs/export-summary',
      data: { run_ids: runIds, format },
      format,
      fallbackName: 'eval_runs_results',
    })
  },
  getResultTrace(resultId: string, project?: string) {
    return api.get<RunDetail>(`/eval/results/${resultId}/trace`, {
      params: project ? { project } : undefined,
    })
  },
  backfillTrace(runId: string, project: string) {
    return api.post<{
      run_id: string
      project: string
      matched: number
      scanned: number
      errors: number
      error_kind: 'forbidden' | 'unauthorized' | 'not_found' | 'network' | 'client_init' | 'unknown' | null
      error_message: string | null
    }>(
      `/eval/runs/${runId}/backfill_trace`,
      null,
      { params: { project } },
    )
  },
  stopRun(runId: string) {
    return api.post<{ run_id: string; status: string }>(`/eval/runs/${runId}/stop`)
  },
  syncLangfuseScores(
    runId: string,
    opts?: { push?: boolean; pull_attempts?: number; pull_interval?: number },
  ) {
    return api.post<{
      run_id: string
      push: { traces: number; scores: number; errors: number } | null
      pull: { polls: number; pulled: number }
    }>(`/eval/runs/${runId}/sync_langfuse_scores`, null, {
      params: {
        push: opts?.push ?? false,
        pull_attempts: opts?.pull_attempts ?? 1,
        pull_interval: opts?.pull_interval ?? 5,
      },
    })
  },
  reaggregateRun(runId: string) {
    return api.post<{
      run_id: string
      dimensions: string[]
      tool_usage_count: number
      case_count: number
    }>(`/eval/runs/${runId}/reaggregate`)
  },
  // 补评缺分维度：复用已存 agent 回答，只对评分缺失的维度重打 judge。
  rescoreRun(runId: string) {
    return api.post<{
      run_id: string
      results_scanned: number
      dimensions_recovered: number
      results_completed: number
      results_still_missing: number
      note?: string
    }>(`/eval/runs/${runId}/rescore`)
  },
}
