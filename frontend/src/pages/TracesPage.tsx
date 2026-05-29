import { useState, useEffect, useMemo, useCallback, useRef, memo } from 'react'
import { useToast } from '@/components/ui'
import { tracesApi, datasetsApi } from '@/services'
import { formatApiError, toToastMessage } from '@/lib/errors'
import type { ListRunsRequest, RunSummary, Dataset, RunDetail, RunChildMeta } from '@/types'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'

interface NodeState {
  data?: RunDetail
  loading: boolean
  error?: string
}

type NodeCache = Record<string, NodeState>

// --- sort/stat helpers kept at module scope so they don't re-allocate per render ---

function tsDesc(a: string | null, b: string | null): number {
  const ta = a ? new Date(a).getTime() : 0
  const tb = b ? new Date(b).getTime() : 0
  return tb - ta
}

// Hoare's quickselect — O(n) average for an unordered array.
function quickselect(arr: number[], k: number): number {
  let lo = 0
  let hi = arr.length - 1
  while (lo < hi) {
    const pivot = arr[(lo + hi) >> 1]
    let i = lo
    let j = hi
    while (i <= j) {
      while (arr[i] < pivot) i++
      while (arr[j] > pivot) j--
      if (i <= j) {
        const t = arr[i]; arr[i] = arr[j]; arr[j] = t
        i++; j--
      }
    }
    if (k <= j) hi = j
    else if (k >= i) lo = i
    else return arr[k]
  }
  return arr[k]
}

interface LatencyStat {
  // General-purpose "seconds distribution" stats — used for both end-to-end
  // latency and time-to-first-token, since both are per-run seconds values.
  model: string
  count: number
  coveredQuestions: number  // distinct input_previews in this model's slice
  min: number
  max: number
  avg: number
  median: number
  p95: number
  variance: number
}

function computeLatencyStats(
  values: number[],
  model: string,
  coveredQuestions: number,
): LatencyStat {
  // Welford pass + min/max in a single scan — no sort yet.
  let min = Infinity
  let max = -Infinity
  let mean = 0
  let m2 = 0
  let n = 0
  for (const v of values) {
    n += 1
    const delta = v - mean
    mean += delta / n
    m2 += delta * (v - mean)
    if (v < min) min = v
    if (v > max) max = v
  }
  const variance = n > 0 ? m2 / n : 0
  // median / p95 need partial order → use quickselect on a single scratch copy.
  const scratch = values.slice()
  const medianIdx = Math.floor(scratch.length / 2)
  const medianRaw = scratch.length ? quickselect(scratch, medianIdx) : 0
  // For even-length arrays, fall back to a second pick for the lower-mid element.
  let median = medianRaw
  if (scratch.length > 0 && scratch.length % 2 === 0) {
    // scratch is partially reordered around medianIdx; scan left half for its max
    let lowerMax = -Infinity
    for (let i = 0; i < medianIdx; i++) if (scratch[i] > lowerMax) lowerMax = scratch[i]
    median = (lowerMax + medianRaw) / 2
  }
  const p95Scratch = values.slice()
  const p95Idx = Math.min(p95Scratch.length - 1, Math.floor(p95Scratch.length * 0.95))
  const p95 = p95Scratch.length ? quickselect(p95Scratch, p95Idx) : 0
  return {
    model,
    count: n,
    coveredQuestions,
    min: n ? +min.toFixed(2) : 0,
    max: n ? +max.toFixed(2) : 0,
    avg: +mean.toFixed(2),
    median: +median.toFixed(2),
    p95: +p95.toFixed(2),
    variance: +variance.toFixed(3),
  }
}

export default function TracesPage() {
  const toast = useToast()
  const [projectName, setProjectName] = useState('')
  const [allRuns, setAllRuns] = useState<RunSummary[]>([])
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [showImportModal, setShowImportModal] = useState(false)
  const [importTarget, setImportTarget] = useState('')
  const [newDatasetName, setNewDatasetName] = useState('')
  const [importing, setImporting] = useState(false)
  const [detailRunId, setDetailRunId] = useState<string | null>(null)
  const [nodeCache, setNodeCache] = useState<NodeCache>({})
  const nodeCacheRef = useRef(nodeCache)
  nodeCacheRef.current = nodeCache
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [modelFilter, setModelFilter] = useState('')
  const [sortBy, setSortBy] = useState<'time' | 'latency_asc' | 'latency_desc'>('time')
  const [showChart, setShowChart] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [fillingModels, setFillingModels] = useState(false)
  const [fillModelsMsg, setFillModelsMsg] = useState('')
  // Chart scope filters (independent of list filters)
  const [chartRecentN, setChartRecentN] = useState<number>(0) // 0 = all
  const [chartStatus, setChartStatus] = useState<'all' | 'success' | 'error'>('all')
  const [chartSelectedModels, setChartSelectedModels] = useState<Set<string>>(new Set())
  const [chartQuestions, setChartQuestions] = useState<Set<string>>(new Set()) // empty = no question filter
  const [excludeTail5, setExcludeTail5] = useState(false)
  const [showQuestionPicker, setShowQuestionPicker] = useState(false)
  const [questionPickerSearch, setQuestionPickerSearch] = useState('')
  const [questionPickerCrossOnly, setQuestionPickerCrossOnly] = useState(false)

  useEffect(() => {
    datasetsApi.list().then(res => setDatasets(res.data)).catch(() => {})
  }, [])

  const fetchRuns = async (mode: 'fresh' | 'more' = 'fresh') => {
    if (!projectName.trim()) return
    setError('')
    if (mode === 'fresh') setLoading(true)
    else setLoadingMore(true)
    try {
      const req: ListRunsRequest = {
        project_name: projectName,
        limit: 50,
        page: 1,
        page_size: 50,
        status: 'success',
      }
      if (mode === 'more' && allRuns.length > 0) {
        // Use the earliest already-loaded start_time as the upper bound
        const earliest = allRuns
          .map(r => r.start_time)
          .filter((x): x is string => !!x)
          .sort()[0]
        if (earliest) req.end_time = earliest
      }
      const res = await tracesApi.listRuns(req)
      const newItems = res.data.items
      if (mode === 'fresh') {
        // Assume server returns newest-first; enforce the invariant defensively.
        const sorted = [...newItems].sort((a, b) => tsDesc(a.start_time, b.start_time))
        setAllRuns(sorted)
        setPage(1)
        setSelectedIds(new Set())
      } else {
        // Dedupe by id — LangSmith may return the boundary run again
        setAllRuns(prev => {
          const seen = new Set(prev.map(r => r.id))
          const merged = prev.concat(newItems.filter(r => !seen.has(r.id)))
          merged.sort((a, b) => tsDesc(a.start_time, b.start_time))
          return merged
        })
      }
      setHasMore(newItems.length >= 50)
    } catch (err: unknown) {
      setError(toToastMessage(formatApiError(err, { fallbackMessage: '查询失败' })))
    } finally {
      setLoading(false)
      setLoadingMore(false)
    }
  }

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    await fetchRuns('fresh')
  }

  const handleClear = () => {
    setAllRuns([])
    setSelectedIds(new Set())
    setNodeCache({})
    setExpanded(new Set())
    setHasMore(false)
    setPage(1)
    setError('')
    setModelFilter('')
    setChartRecentN(0)
    setChartStatus('all')
    setChartSelectedModels(new Set())
    setChartQuestions(new Set())
    setExcludeTail5(false)
    setShowQuestionPicker(false)
    setQuestionPickerSearch('')
    setQuestionPickerCrossOnly(false)
    setFillModelsMsg('')
  }

  const handleFillModels = useCallback(async () => {
    if (fillingModels) return
    // Fill what's missing: a run without a model_name OR without a known
    // first_tool_call_s (null could mean "not yet queried" OR "confirmed no
    // tool call" — backend's negative cache makes a second call cheap, so
    // re-fill is safe).
    const needsFill = allRuns.filter(r => !r.model_name || r.first_tool_call_s == null)
    if (needsFill.length === 0) {
      setFillModelsMsg('所有 run 的信息都已完整')
      return
    }
    setFillingModels(true)
    setFillModelsMsg(`正在补齐 ${needsFill.length} 条 model 和首次工具调用时延…（首次约 30-120s）`)
    try {
      const payload = needsFill.map(r => ({ id: r.id, start_time: r.start_time }))
      const res = await tracesApi.fillModels({ project_name: projectName, runs: payload })
      const { models, first_tool_calls, missing: stillMissing } = res.data
      setAllRuns(prev => prev.map(r => {
        const m = r.model_name || models[r.id] || ''
        const t = r.first_tool_call_s ?? (r.id in first_tool_calls ? first_tool_calls[r.id] : null)
        if (m === r.model_name && t === r.first_tool_call_s) return r
        return { ...r, model_name: m, first_tool_call_s: t }
      }))
      const resolvedModels = Object.keys(models).length
      const resolvedTools = Object.keys(first_tool_calls).length
      setFillModelsMsg(
        `补齐 ${resolvedModels}/${needsFill.length} model，${resolvedTools} 条有首次工具调用` +
        (stillMissing.length > 0 ? `，${stillMissing.length} 条无 LLM 子 run` : '')
      )
    } catch (err: unknown) {
      setFillModelsMsg(`补齐失败：${toToastMessage(formatApiError(err, { fallbackMessage: '未知错误' }))}`)
    } finally {
      setFillingModels(false)
    }
  }, [allRuns, projectName, fillingModels])

  // Derived: unique model names (O(n), no sort of the main runs list)
  const modelNames = useMemo(() => {
    const names = new Set<string>()
    for (const r of allRuns) {
      if (r.model_name) names.add(r.model_name)
    }
    return Array.from(names).sort()
  }, [allRuns])

  // Derived: filtered and sorted runs.
  // `allRuns` is already time-desc, so the 'time' branch is zero-cost.
  const filteredRuns = useMemo(() => {
    const base = modelFilter ? allRuns.filter(r => r.model_name === modelFilter) : allRuns
    if (sortBy === 'time') return base
    const copy = base.slice()
    if (sortBy === 'latency_asc') {
      copy.sort((a, b) => (a.latency_s ?? Infinity) - (b.latency_s ?? Infinity))
    } else {
      copy.sort((a, b) => (b.latency_s ?? 0) - (a.latency_s ?? 0))
    }
    return copy
  }, [allRuns, modelFilter, sortBy])

  const total = filteredRuns.length
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const pageRuns = useMemo(
    () => filteredRuns.slice((page - 1) * pageSize, page * pageSize),
    [filteredRuns, page, pageSize]
  )

  // Derived: runs scoped for the chart block (independent of list filters).
  // Relies on `allRuns` being time-desc so "最近 N 条" is a slice, not a sort.
  // `excludeTail5` is applied AFTER all other filters: per-model drop the
  // slowest 5% by latency. This keeps the comparison robust to a handful of
  // stuck / timeout runs that would otherwise dominate avg and P95. Applied
  // symmetrically to all three charts (latency / ttft / tool-call) so they
  // agree on which rows count.
  const chartScopedRuns = useMemo(() => {
    const needsStatusFilter = chartStatus !== 'all'
    const needsModelFilter = chartSelectedModels.size > 0
    const needsQuestionFilter = chartQuestions.size > 0
    const scoped: RunSummary[] = []
    const limit = chartRecentN > 0 ? chartRecentN : Infinity
    for (const r of allRuns) {
      if (scoped.length >= limit) break
      if (needsStatusFilter && r.status !== chartStatus) continue
      if (needsModelFilter && !chartSelectedModels.has(r.model_name || '(unknown)')) continue
      if (needsQuestionFilter && !chartQuestions.has(r.input_preview)) continue
      scoped.push(r)
    }
    if (!excludeTail5) return scoped
    // Per-model tail trim: drop the slowest 5% (by latency_s) from each group.
    // Small groups round down to 0 dropped, so this won't wipe a model with
    // only a few runs.
    const byModel = new Map<string, RunSummary[]>()
    for (const r of scoped) {
      const m = r.model_name || '(unknown)'
      const list = byModel.get(m)
      if (list) list.push(r)
      else byModel.set(m, [r])
    }
    const dropIds = new Set<string>()
    for (const group of byModel.values()) {
      const dropN = Math.floor(group.length * 0.05)
      if (dropN === 0) continue
      const sorted = [...group].sort((a, b) => (b.latency_s ?? -Infinity) - (a.latency_s ?? -Infinity))
      for (let i = 0; i < dropN; i++) dropIds.add(sorted[i].id)
    }
    if (dropIds.size === 0) return scoped
    return scoped.filter(r => !dropIds.has(r.id))
  }, [allRuns, chartStatus, chartSelectedModels, chartRecentN, chartQuestions, excludeTail5])

  // Derived: all distinct questions (input_preview) found in allRuns, with how
  // many distinct models ran each and total run count. Used by the question
  // picker; supports filtering "cross-model only" in the picker UI.
  // Sorted by cross-model preference (more models first, then more runs).
  const allQuestions = useMemo(() => {
    const byPreview = new Map<string, { models: Set<string>; count: number }>()
    for (const r of allRuns) {
      const p = r.input_preview
      if (!p) continue
      const m = r.model_name || '(unknown)'
      let entry = byPreview.get(p)
      if (!entry) {
        entry = { models: new Set(), count: 0 }
        byPreview.set(p, entry)
      }
      entry.models.add(m)
      entry.count++
    }
    const result: { preview: string; modelCount: number; runCount: number }[] = []
    for (const [preview, entry] of byPreview) {
      result.push({ preview, modelCount: entry.models.size, runCount: entry.count })
    }
    result.sort((a, b) => b.modelCount - a.modelCount || b.runCount - a.runCount)
    return result
  }, [allRuns])

  // Questions shown inside the picker panel, after applying its own filters
  // (search text + "cross-model only" toggle). Kept separate from `allQuestions`
  // so that clearing a filter doesn't recompute the base index.
  const pickerQuestions = useMemo(() => {
    const needle = questionPickerSearch.trim().toLowerCase()
    return allQuestions.filter(q => {
      if (questionPickerCrossOnly && q.modelCount < 2) return false
      if (needle && !q.preview.toLowerCase().includes(needle)) return false
      return true
    })
  }, [allQuestions, questionPickerSearch, questionPickerCrossOnly])

  // Derived: latency + first-token + first-tool-call stats per model, in one
  // pass. All three charts share the same per-model question-coverage count.
  const { latencyStats, firstTokenStats, firstToolCallStats } = useMemo(() => {
    type Bucket = { latency: number[]; ttft: number[]; firstTool: number[]; questions: Set<string> }
    const groups = new Map<string, Bucket>()
    for (const run of chartScopedRuns) {
      const model = run.model_name || '(unknown)'
      let bucket = groups.get(model)
      if (!bucket) {
        bucket = { latency: [], ttft: [], firstTool: [], questions: new Set() }
        groups.set(model, bucket)
      }
      if (run.latency_s != null) bucket.latency.push(run.latency_s)
      if (run.first_token_s != null) bucket.ttft.push(run.first_token_s)
      if (run.first_tool_call_s != null) bucket.firstTool.push(run.first_tool_call_s)
      if (run.input_preview) bucket.questions.add(run.input_preview)
    }
    const latency: LatencyStat[] = []
    const ttft: LatencyStat[] = []
    const tool: LatencyStat[] = []
    for (const [model, b] of groups) {
      if (b.latency.length > 0) latency.push(computeLatencyStats(b.latency, model, b.questions.size))
      if (b.ttft.length > 0) ttft.push(computeLatencyStats(b.ttft, model, b.questions.size))
      if (b.firstTool.length > 0) tool.push(computeLatencyStats(b.firstTool, model, b.questions.size))
    }
    latency.sort((a, b) => a.avg - b.avg)
    ttft.sort((a, b) => a.avg - b.avg)
    tool.sort((a, b) => a.avg - b.avg)
    return { latencyStats: latency, firstTokenStats: ttft, firstToolCallStats: tool }
  }, [chartScopedRuns])

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const toggleSelectAll = useCallback(() => {
    setSelectedIds(prev => {
      if (prev.size === pageRuns.length && pageRuns.length > 0) return new Set()
      return new Set(pageRuns.map(r => r.id))
    })
  }, [pageRuns])

  const handleImport = async () => {
    const target = importTarget === '__new__' ? newDatasetName.trim() : importTarget
    if (!target) return
    setImporting(true)
    try {
      if (importTarget === '__new__') {
        await datasetsApi.create({ name: target, description: `Imported from ${projectName} traces`, source_project: projectName })
      }
      const res = await tracesApi.import({ dataset: target, run_ids: Array.from(selectedIds), project_name: projectName })
      setShowImportModal(false)
      setSelectedIds(new Set())
      setImportTarget('')
      setNewDatasetName('')
      toast.success(`成功导入 ${res.data.imported} 条用例到 ${target}`)
      datasetsApi.list().then(r => setDatasets(r.data)).catch(() => {})
    } catch (err: unknown) {
      toast.error(toToastMessage(formatApiError(err, { fallbackTitle: '导入失败', fallbackMessage: '导入失败' })))
    } finally {
      setImporting(false)
    }
  }

  const fetchNode = useCallback(async (runId: string) => {
    setNodeCache(prev => (prev[runId]?.data || prev[runId]?.loading) ? prev : { ...prev, [runId]: { loading: true } })
    try {
      const res = await tracesApi.getDetail({ run_id: runId, project_name: projectName || undefined })
      setNodeCache(prev => ({ ...prev, [runId]: { loading: false, data: res.data } }))
    } catch (err: unknown) {
      const msg = toToastMessage(formatApiError(err, { fallbackMessage: '加载失败' }))
      setNodeCache(prev => ({ ...prev, [runId]: { loading: false, error: msg } }))
    }
  }, [projectName])

  const openDetail = useCallback((runId: string) => {
    setDetailRunId(runId)
    if (!nodeCacheRef.current[runId]?.data) fetchNode(runId)
  }, [fetchNode])

  const closeDetail = useCallback(() => {
    setDetailRunId(null)
    setExpanded(new Set())
  }, [])

  const toggleExpand = useCallback((runId: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(runId)) {
        next.delete(runId)
      } else {
        next.add(runId)
        const cached = nodeCacheRef.current[runId]
        if (!cached?.data && !cached?.loading) {
          fetchNode(runId)
        }
      }
      return next
    })
  }, [fetchNode])

  return (
    <div>
      <header className="mb-6">
        <h1 className="page-title">调用轨迹</h1>
        <p className="page-subtitle">执行记录 · 性能指标</p>
      </header>

      <form onSubmit={handleSearch} className="toolbar">
        <input
          placeholder="项目名称…"
          value={projectName}
          onChange={(e) => setProjectName(e.target.value)}
          required
          className="input-sm flex-1 max-w-[280px]"
        />
        <button
          type="submit"
          disabled={loading}
          className="inline-flex items-center justify-center h-8 px-3 text-[13px] font-medium rounded-md bg-accent text-accent-fg border border-transparent hover:bg-accent-hover active:opacity-90 disabled:opacity-40 transition-[background-color,color,box-shadow] duration-150 ease-standard focus-visible:shadow-focus focus-visible:outline-none gap-1.5"
        >
          {loading ? (
            <span className="inline-block w-3 h-3 border border-white/40 border-t-white rounded-full animate-spin" />
          ) : '查询'}
        </button>
        {allRuns.length > 0 && (
          <button
            type="button"
            onClick={handleClear}
            className="inline-flex items-center justify-center h-8 px-3 text-[13px] rounded-md border border-border bg-surface text-text-secondary hover:bg-surface-hover transition-colors duration-150 ease-standard focus-visible:shadow-focus focus-visible:outline-none"
          >
            清空
          </button>
        )}
        {allRuns.length > 0 && allRuns.some(r => !r.model_name || r.first_tool_call_s == null) && (
          <button
            type="button"
            onClick={handleFillModels}
            disabled={fillingModels}
            title="用多轮时间窗口补齐 model_name 与首次工具调用时延。首次 30-120s，结果缓存 1 小时。"
            className="inline-flex items-center gap-1.5 h-8 px-3 text-[13px] rounded-md border border-border bg-surface text-text-secondary hover:bg-surface-hover disabled:opacity-40 transition-colors duration-150 ease-standard"
          >
            {fillingModels ? (
              <>
                <span className="inline-block w-3 h-3 border border-accent/40 border-t-accent rounded-full animate-spin" />
                补齐中
              </>
            ) : `补齐信息 (${allRuns.filter(r => !r.model_name || r.first_tool_call_s == null).length})`}
          </button>
        )}
        {selectedIds.size > 0 && (
          <button
            type="button"
            onClick={() => setShowImportModal(true)}
            className="inline-flex items-center h-8 px-3 text-[13px] font-medium rounded-md bg-positive text-white border border-transparent hover:opacity-90 transition-opacity"
          >
            导入到备选数据集 ({selectedIds.size})
          </button>
        )}
      </form>

      {fillModelsMsg && (
        <p className="text-[12px] text-text-tertiary mb-3 animate-fade-in">{fillModelsMsg}</p>
      )}

      {allRuns.length > 0 && (
        <div className="toolbar">
          <select
            value={modelFilter}
            onChange={e => { setModelFilter(e.target.value); setPage(1) }}
            className="select-sm"
          >
            <option value="">全部模型</option>
            {modelNames.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <select
            value={sortBy}
            onChange={e => setSortBy(e.target.value as typeof sortBy)}
            className="select-sm"
          >
            <option value="time">按时间排序</option>
            <option value="latency_asc">时延 ↑</option>
            <option value="latency_desc">时延 ↓</option>
          </select>
          <button
            onClick={() => setShowChart(v => !v)}
            className={`inline-flex items-center h-8 px-3 text-[12px] rounded-md border transition-colors duration-150 ease-standard ${
              showChart
                ? 'border-accent text-accent bg-accent/10'
                : 'border-border text-text-secondary hover:bg-surface-hover'
            }`}
          >
            {showChart ? '隐藏对比图' : '模型性能对比'}
          </button>
        </div>
      )}

      {error && <p className="text-[12px] text-negative mb-3 animate-fade-in">{error}</p>}

      {showChart && (
        <div className="card p-5 mb-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-[13px] font-semibold text-text-primary">模型性能对比</h3>
            <span className="text-[11px] text-text-tertiary">
              对比样本：{chartScopedRuns.length} 条
              {chartQuestions.size > 0 && ` · ${chartQuestions.size} 个问题`}
              （总加载 {allRuns.length} 条）
            </span>
          </div>

          {/* Chart scope filters */}
          <div className="flex items-start gap-4 flex-wrap mb-4 p-3 bg-fill/5 rounded-lg border border-border">
            <div className="flex items-center gap-2">
              <label className="page-eyebrow">最近</label>
              <select
                value={chartRecentN}
                onChange={e => setChartRecentN(Number(e.target.value))}
                className="select-sm"
              >
                <option value={0}>全部</option>
                <option value={10}>10 条</option>
                <option value={20}>20 条</option>
                <option value={50}>50 条</option>
                <option value={100}>100 条</option>
                <option value={200}>200 条</option>
              </select>
            </div>

            <div className="flex items-center gap-2">
              <label className="page-eyebrow">状态</label>
              <select
                value={chartStatus}
                onChange={e => setChartStatus(e.target.value as typeof chartStatus)}
                className="select-sm"
              >
                <option value="all">全部</option>
                <option value="success">success</option>
                <option value="error">error</option>
              </select>
            </div>

            <div className="flex items-start gap-2 flex-1 min-w-[200px]">
              <label className="page-eyebrow mt-1.5 shrink-0">模型</label>
              <div className="flex flex-wrap gap-1.5">
                {modelNames.length === 0 ? (
                  <span className="text-[11px] text-text-tertiary">无可选模型</span>
                ) : modelNames.map(m => {
                  const on = chartSelectedModels.has(m)
                  return (
                    <button
                      key={m}
                      type="button"
                      onClick={() => {
                        setChartSelectedModels(prev => {
                          const next = new Set(prev)
                          if (next.has(m)) next.delete(m); else next.add(m)
                          return next
                        })
                      }}
                      className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors duration-150 ease-standard ${
                        on
                          ? 'border-transparent bg-accent text-accent-fg'
                          : 'border-border bg-surface text-text-secondary hover:bg-surface-hover'
                      }`}
                    >
                      {m}
                    </button>
                  )
                })}
                {chartSelectedModels.size > 0 && (
                  <button
                    type="button"
                    onClick={() => setChartSelectedModels(new Set())}
                    className="text-[11px] px-2 py-0.5 rounded-full border border-border text-text-tertiary hover:bg-surface-hover"
                  >
                    清除
                  </button>
                )}
              </div>
            </div>

            <div className="flex items-center gap-2 w-full">
              <label className="inline-flex items-center gap-2 text-[12px] text-text-secondary cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={excludeTail5}
                  onChange={e => setExcludeTail5(e.target.checked)}
                  className="w-3.5 h-3.5 accent-accent"
                />
                <span>排除各模型最慢 5%（避免 outlier 拉高 avg / P95）</span>
              </label>
              {excludeTail5 && (
                <span className="text-[11px] text-text-tertiary">
                  按 latency 倒序每模型去头；小样本（&lt;20）floor 为 0 不影响
                </span>
              )}
            </div>

            <div className="flex flex-col gap-2 w-full">
              <div className="flex items-center gap-2 flex-wrap">
                <label className="page-eyebrow shrink-0">问题集</label>
                <button
                  type="button"
                  onClick={() => setShowQuestionPicker(v => !v)}
                  disabled={allQuestions.length === 0}
                  className="inline-flex items-center gap-1.5 h-7 px-2.5 text-[12px] border border-border rounded-md bg-surface text-text-primary hover:bg-surface-hover disabled:opacity-50 transition-colors"
                  title="勾选一批问题后，每个模型用自己在这批问题里的 runs 各算一次 latency 指标"
                >
                  {chartQuestions.size === 0
                    ? (allQuestions.length === 0 ? '无可选问题' : `选择问题（共 ${allQuestions.length}）`)
                    : `已选 ${chartQuestions.size} / ${allQuestions.length}`}
                  <span className="text-text-tertiary">{showQuestionPicker ? '▴' : '▾'}</span>
                </button>
                {chartQuestions.size > 0 && (
                  <button
                    type="button"
                    onClick={() => setChartQuestions(new Set())}
                    className="text-[11px] px-2 py-0.5 rounded-full border border-border text-text-tertiary hover:bg-surface-hover"
                  >
                    清空选择
                  </button>
                )}
                <span className="text-[11px] text-text-tertiary ml-auto">
                  空=不按问题过滤；多选=每模型在自己的覆盖子集上算 latency
                </span>
              </div>

              {showQuestionPicker && (
                <div className="border border-border rounded-lg bg-surface p-3 flex flex-col gap-2 max-h-[360px]">
                  <div className="flex items-center gap-2 flex-wrap">
                    <input
                      type="text"
                      placeholder="搜索问题内容…"
                      value={questionPickerSearch}
                      onChange={e => setQuestionPickerSearch(e.target.value)}
                      className="input-sm flex-1 min-w-[200px]"
                    />
                    <label className="inline-flex items-center gap-1.5 text-[11px] text-text-secondary cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={questionPickerCrossOnly}
                        onChange={e => setQuestionPickerCrossOnly(e.target.checked)}
                        className="w-3 h-3 accent-accent"
                      />
                      仅跨模型问题（≥2 models）
                    </label>
                    <div className="flex items-center gap-1 ml-auto">
                      <button
                        type="button"
                        onClick={() => {
                          setChartQuestions(prev => {
                            const next = new Set(prev)
                            for (const q of pickerQuestions) next.add(q.preview)
                            return next
                          })
                        }}
                        disabled={pickerQuestions.length === 0}
                        className="text-[11px] px-2 py-0.5 rounded-full border border-border text-text-secondary hover:bg-surface-hover disabled:opacity-40"
                      >
                        全选当前列表
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setChartQuestions(prev => {
                            const next = new Set(prev)
                            for (const q of pickerQuestions) next.delete(q.preview)
                            return next
                          })
                        }}
                        disabled={pickerQuestions.length === 0}
                        className="text-[11px] px-2 py-0.5 rounded-full border border-border text-text-secondary hover:bg-surface-hover disabled:opacity-40"
                      >
                        取消选择
                      </button>
                    </div>
                  </div>
                  <div className="text-[11px] text-text-tertiary">
                    显示 {pickerQuestions.length} / {allQuestions.length} 个问题
                    {chartQuestions.size > 0 && `（当前共选中 ${chartQuestions.size} 个）`}
                  </div>
                  <div className="flex-1 overflow-y-auto border border-border rounded-md bg-fill/5 divide-y divide-separator">
                    {pickerQuestions.length === 0 ? (
                      <div className="text-center py-6 text-[12px] text-text-tertiary">
                        {questionPickerCrossOnly
                          ? '当前搜索下没有跨模型问题；取消「仅跨模型」试试'
                          : '没有匹配的问题'}
                      </div>
                    ) : pickerQuestions.map(q => {
                      const checked = chartQuestions.has(q.preview)
                      const isCross = q.modelCount >= 2
                      return (
                        <label
                          key={q.preview}
                          className="flex items-start gap-2 py-1.5 px-2 hover:bg-fill/10 cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => {
                              setChartQuestions(prev => {
                                const next = new Set(prev)
                                if (next.has(q.preview)) next.delete(q.preview)
                                else next.add(q.preview)
                                return next
                              })
                            }}
                            className="mt-0.5 w-3 h-3 accent-accent shrink-0"
                          />
                          <span className={`text-[10px] px-1.5 py-0.5 rounded shrink-0 tabular-nums ${
                            isCross ? 'bg-accent text-accent-fg' : 'bg-fill/15 text-text-tertiary'
                          }`}>
                            {q.modelCount}m · {q.runCount}r
                          </span>
                          <span className="text-[11px] text-text-primary flex-1 break-all">
                            {q.preview.length > 180 ? q.preview.slice(0, 180) + '…' : q.preview}
                          </span>
                        </label>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>

          {latencyStats.length === 0 && firstTokenStats.length === 0 && firstToolCallStats.length === 0 ? (
            <div className="empty-state">当前筛选条件下无数据</div>
          ) : (
            <div className="flex flex-col gap-6">
              <DistributionChart
                title="端到端 Latency"
                stats={latencyStats}
                emptyHint="所选数据里没有 latency 字段"
                pickedQuestions={chartQuestions.size}
              />
              <DistributionChart
                title="首 Token 时延 (TTFT)"
                stats={firstTokenStats}
                emptyHint="所选数据里没有 first_token_s —— 这类 run 多见于非流式调用或采集失败"
                pickedQuestions={chartQuestions.size}
              />
              <DistributionChart
                title="首次工具调用时延"
                stats={firstToolCallStats}
                emptyHint="所选数据里没有 first_tool_call_s —— 未调用工具的 run，或尚未点「补齐信息」"
                pickedQuestions={chartQuestions.size}
              />
            </div>
          )}
        </div>
      )}

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <th className="w-10 text-center">
                <input
                  type="checkbox"
                  checked={pageRuns.length > 0 && selectedIds.size === pageRuns.length}
                  onChange={toggleSelectAll}
                  className="w-3.5 h-3.5 accent-accent"
                />
              </th>
              <th>名称</th>
              <th>模型</th>
              <th>状态</th>
              <th>输入</th>
              <th>输出</th>
              <th className="text-right">时延</th>
              <th className="text-right">token</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            {pageRuns.map((run) => (
              <tr key={run.id} className="cursor-default">
                <td className="text-center">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(run.id)}
                    onChange={() => toggleSelect(run.id)}
                    className="w-3.5 h-3.5 accent-accent"
                  />
                </td>
                <td className="max-w-[140px] truncate">
                  <button onClick={() => openDetail(run.id)} className="text-left hover:text-accent transition-colors">
                    {run.name}
                  </button>
                </td>
                <td className="text-text-secondary text-[11px] whitespace-nowrap">
                  {run.model_name || '—'}
                </td>
                <td>
                  <span className={
                    run.status === 'success' ? 'badge badge-positive'
                    : run.status === 'error' ? 'badge badge-negative'
                    : 'badge badge-neutral'
                  }>
                    {run.status}
                  </span>
                </td>
                <td className="text-text-secondary text-[11px] max-w-[180px] truncate">
                  <button onClick={() => openDetail(run.id)} className="text-left hover:text-accent transition-colors truncate block w-full">
                    {run.input_preview || '—'}
                  </button>
                </td>
                <td className="text-text-secondary text-[11px] max-w-[180px] truncate">
                  <button onClick={() => openDetail(run.id)} className="text-left hover:text-accent transition-colors truncate block w-full">
                    {run.output_preview || '—'}
                  </button>
                </td>
                <td className="text-right text-text-secondary tabular-nums whitespace-nowrap">
                  {run.latency_s ? `${run.latency_s.toFixed(2)}s` : '—'}
                </td>
                <td className="text-right text-text-secondary tabular-nums whitespace-nowrap">
                  {run.total_tokens ?? '—'}
                </td>
                <td className="text-text-tertiary text-[11px] whitespace-nowrap">
                  {run.start_time ? new Date(run.start_time).toLocaleString() : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {pageRuns.length === 0 && !loading && (
          <div className="empty-state">输入项目名称查询调用轨迹</div>
        )}
      </div>

      {total > 0 && (
        <div className="flex items-center justify-between mt-4 text-[12px] text-text-secondary">
          <span>共 {total} 条结果（已加载 {allRuns.length}）</span>
          <div className="flex items-center gap-2">
            <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="pager-btn">上一页</button>
            <span className="tabular-nums">{page} / {totalPages}</span>
            <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)} className="pager-btn">下一页</button>
            {hasMore && (
              <button
                onClick={() => fetchRuns('more')}
                disabled={loadingMore}
                className="ml-2 inline-flex items-center gap-1.5 h-7 px-2.5 text-[11px] rounded-md border border-accent text-accent bg-accent/10 hover:bg-accent/15 disabled:opacity-40 transition-colors"
              >
                {loadingMore ? (
                  <>
                    <span className="inline-block w-3 h-3 border border-accent/40 border-t-accent rounded-full animate-spin" />
                    加载中
                  </>
                ) : '加载更早 50 条'}
              </button>
            )}
          </div>
        </div>
      )}

      {showImportModal && (
        <div className="fixed inset-0 z-50 bg-black/30 dark:bg-black/55 backdrop-blur-[6px] flex items-center justify-center animate-overlay-in" onClick={() => setShowImportModal(false)}>
          <div className="bg-bg-elevated border border-border/60 rounded-2xl p-6 w-[400px] shadow-xl animate-dialog-in" onClick={e => e.stopPropagation()}>
            <h3 className="text-[17px] font-display font-semibold tracking-[-0.4px] text-text-primary mb-1">导入到数据集</h3>
            <p className="text-[12px] text-text-secondary mb-4">已选择 {selectedIds.size} 条 Runs，将提取为测试用例导入目标数据集。</p>
            <div className="mb-4">
              <label className="field-label">目标数据集</label>
              <select
                value={importTarget}
                onChange={e => setImportTarget(e.target.value)}
                className="input"
              >
                <option value="">选择数据集…</option>
                {datasets.map(d => (
                  <option key={d.id} value={d.name}>{d.name} ({d.example_count} 条)</option>
                ))}
                <option value="__new__">+ 新建数据集</option>
              </select>
            </div>
            {importTarget === '__new__' && (
              <div className="mb-4">
                <label className="field-label">新数据集名称</label>
                <input
                  value={newDatasetName}
                  onChange={e => setNewDatasetName(e.target.value)}
                  placeholder="输入数据集名称…"
                  className="input"
                />
              </div>
            )}
            <div className="flex justify-end gap-2 mt-6">
              <button
                onClick={() => setShowImportModal(false)}
                className="inline-flex items-center h-8 px-3 text-[13px] rounded-md border border-border bg-surface text-text-primary hover:bg-surface-hover transition-colors"
              >
                取消
              </button>
              <button
                onClick={handleImport}
                disabled={importing || (!importTarget || (importTarget === '__new__' && !newDatasetName.trim()))}
                className="inline-flex items-center h-8 px-3 text-[13px] font-medium rounded-md bg-accent text-accent-fg border border-transparent hover:bg-accent-hover disabled:opacity-40 transition-colors"
              >
                {importing ? `导入中 (${selectedIds.size} 条)…` : '确认导入'}
              </button>
            </div>
          </div>
        </div>
      )}

      {detailRunId && (
        <RunDetailModal
          rootId={detailRunId}
          projectName={projectName}
          nodeCache={nodeCache}
          expanded={expanded}
          onClose={closeDetail}
          onToggle={toggleExpand}
          onRetry={fetchNode}
        />
      )}
    </div>
  )
}

interface RunDetailModalProps {
  rootId: string
  projectName: string
  nodeCache: NodeCache
  expanded: Set<string>
  onClose: () => void
  onToggle: (id: string) => void
  onRetry: (id: string) => void
}

function RunDetailModal({ rootId, projectName, nodeCache, expanded, onClose, onToggle, onRetry }: RunDetailModalProps) {
  const rootState = nodeCache[rootId]
  return (
    <div className="fixed inset-0 z-50 bg-black/30 dark:bg-black/55 backdrop-blur-[6px] flex items-center justify-center animate-overlay-in" onClick={onClose}>
      <div className="bg-bg-elevated border border-border/60 rounded-2xl w-[860px] max-w-[95vw] max-h-[88vh] overflow-y-auto shadow-xl animate-dialog-in" onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-bg-elevated/95 backdrop-blur-[6px] border-b border-separator px-6 py-4 flex justify-between items-center z-10">
          <h3 className="text-[17px] font-display font-semibold tracking-[-0.4px] text-text-primary">调用详情</h3>
          <button
            onClick={onClose}
            aria-label="关闭"
            className="inline-flex h-8 w-8 items-center justify-center rounded-full text-text-secondary hover:bg-fill/10 hover:text-text-primary transition-colors"
          >
            <svg viewBox="0 0 20 20" width="14" height="14" fill="none" aria-hidden="true">
              <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className="p-6">
          {rootState?.loading && !rootState.data && <LoadingSkeleton />}
          {rootState?.error && !rootState.data && (
            <div className="text-negative text-[12px]">
              {rootState.error}
              <button onClick={() => onRetry(rootId)} className="ml-3 underline">重试</button>
            </div>
          )}
          {rootState?.data && (
            <>
              <RunDetailBody detail={rootState.data} />
              <div className="mt-6">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-[12px] font-semibold text-text-primary">子节点 ({rootState.data.children.length})</h4>
                  {rootState.data.children_truncated && (
                    <span className="badge badge-warning">已截断前 100 个子节点</span>
                  )}
                </div>
                {rootState.data.children.length === 0 ? (
                  <div className="text-[11px] text-text-tertiary">无子节点</div>
                ) : (
                  <div className="border border-border rounded-lg overflow-hidden">
                    {rootState.data.children.map(c => (
                      <RunNodeRow
                        key={c.id}
                        meta={c}
                        depth={0}
                        projectName={projectName}
                        isOpen={expanded.has(c.id)}
                        state={nodeCache[c.id]}
                        nodeCache={nodeCache}
                        expanded={expanded}
                        onToggle={onToggle}
                        onRetry={onRetry}
                      />
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const RUN_TYPE_COLORS: Record<string, { border: string; badge: string }> = {
  llm: { border: 'rgb(var(--accent))', badge: 'bg-accent/10 text-accent' },
  tool: { border: 'rgb(var(--positive))', badge: 'bg-positive/10 text-positive' },
  chain: { border: 'rgb(var(--info))', badge: 'bg-info/15 text-info' },
  retriever: { border: 'rgb(var(--warning))', badge: 'bg-warning/15 text-warning' },
  prompt: { border: 'rgb(var(--negative))', badge: 'bg-negative/10 text-negative' },
}
const DEFAULT_TYPE_COLOR = { border: 'rgb(var(--fill) / 0.4)', badge: 'badge badge-neutral' }

interface DistributionChartProps {
  title: string
  stats: LatencyStat[]
  emptyHint: string
  pickedQuestions: number  // 0 means no question filter active
}

const DistributionChart = memo(function DistributionChart({ title, stats, emptyHint, pickedQuestions }: DistributionChartProps) {
  if (stats.length === 0) {
    return (
      <div>
        <h4 className="text-[11px] font-medium text-text-secondary mb-2">{title}</h4>
        <div className="text-center py-6 text-[11px] text-text-tertiary border border-dashed border-border rounded-[4px]">
          {emptyHint}
        </div>
      </div>
    )
  }
  return (
    <div>
      <h4 className="text-[11px] font-medium text-text-secondary mb-2">{title}</h4>
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={stats} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--separator) / 0.3)" />
          <XAxis dataKey="model" tick={{ fontSize: 10 }} />
          <YAxis tick={{ fontSize: 10 }} label={{ value: '秒', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
          <Tooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} />
          <Legend wrapperStyle={{ fontSize: 10 }} />
          <Bar dataKey="min" name="最小" fill="rgb(var(--positive))" radius={[2, 2, 0, 0]} />
          <Bar dataKey="avg" name="平均" fill="rgb(var(--accent))" radius={[2, 2, 0, 0]} />
          <Bar dataKey="median" name="中位" fill="rgb(var(--accent-hover))" radius={[2, 2, 0, 0]} />
          <Bar dataKey="p95" name="P95" fill="rgb(var(--warning))" radius={[2, 2, 0, 0]} />
          <Bar dataKey="max" name="最大" fill="rgb(var(--negative))" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-[11px] border-collapse">
          <thead>
            <tr className="text-text-tertiary">
              <th className="text-left py-1.5 px-2 font-normal">模型</th>
              <th className="text-right py-1.5 px-2 font-normal">样本数</th>
              <th className="text-right py-1.5 px-2 font-normal" title="该模型在选中问题集里覆盖了多少个不同的问题 / 当前选中问题总数">
                覆盖问题
              </th>
              <th className="text-right py-1.5 px-2 font-normal">最小</th>
              <th className="text-right py-1.5 px-2 font-normal">平均</th>
              <th className="text-right py-1.5 px-2 font-normal">中位</th>
              <th className="text-right py-1.5 px-2 font-normal">P95</th>
              <th className="text-right py-1.5 px-2 font-normal">最大</th>
              <th className="text-right py-1.5 px-2 font-normal">方差</th>
            </tr>
          </thead>
          <tbody>
            {stats.map(s => (
              <tr key={s.model} className="border-t border-border">
                <td className="py-1.5 px-2 font-medium">{s.model}</td>
                <td className="py-1.5 px-2 text-right">{s.count}</td>
                <td className="py-1.5 px-2 text-right">
                  {pickedQuestions > 0 ? `${s.coveredQuestions} / ${pickedQuestions}` : s.coveredQuestions}
                </td>
                <td className="py-1.5 px-2 text-right">{s.min}s</td>
                <td className="py-1.5 px-2 text-right">{s.avg}s</td>
                <td className="py-1.5 px-2 text-right">{s.median}s</td>
                <td className="py-1.5 px-2 text-right">{s.p95}s</td>
                <td className="py-1.5 px-2 text-right">{s.max}s</td>
                <td className="py-1.5 px-2 text-right">{s.variance}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
})

interface RunNodeRowProps {
  meta: RunChildMeta
  depth: number
  projectName: string
  isOpen: boolean
  state: NodeState | undefined
  nodeCache: NodeCache
  expanded: Set<string>
  onToggle: (id: string) => void
  onRetry: (id: string) => void
}

const RunNodeRow = memo(function RunNodeRow({
  meta, depth, projectName, isOpen, state, nodeCache, expanded, onToggle, onRetry,
}: RunNodeRowProps) {
  const color = RUN_TYPE_COLORS[meta.run_type] || DEFAULT_TYPE_COLOR
  const canExpand = meta.has_children

  return (
    <div>
      <div
        className="flex items-center gap-2 py-1.5 border-b border-separator hover:bg-fill/5 cursor-default text-[11px]"
        style={{ paddingLeft: 12 + depth * 16, borderLeft: `2px solid ${color.border}` }}
      >
        <button
          type="button"
          onClick={() => canExpand && onToggle(meta.id)}
          className={`w-4 text-center select-none ${canExpand ? 'text-text-secondary hover:text-accent cursor-pointer' : 'text-transparent'}`}
        >
          {isOpen ? '▾' : '▸'}
        </button>
        <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] tracking-wide uppercase ${color.badge}`}>
          {meta.run_type || '—'}
        </span>
        <span className="flex-1 truncate text-text-primary font-medium">{meta.name || '—'}</span>
        {meta.error && <span className="w-1.5 h-1.5 rounded-full bg-negative" title={meta.error} />}
        <span className="text-text-tertiary tabular-nums">
          {meta.latency_s != null ? `${meta.latency_s.toFixed(2)}s` : '—'}
        </span>
        <span className="text-text-tertiary tabular-nums w-16 text-right">
          {meta.total_tokens != null ? `${meta.total_tokens} tok` : '—'}
        </span>
      </div>

      {isOpen && (
        <div style={{ paddingLeft: 12 + depth * 16 }} className="border-b border-separator">
          {state?.loading && !state.data && <div className="py-3 px-3 text-[11px] text-text-tertiary">加载中…</div>}
          {state?.error && !state.data && (
            <div className="py-3 px-3 text-[11px] text-negative">
              {state.error}
              <button onClick={() => onRetry(meta.id)} className="ml-3 underline">重试</button>
            </div>
          )}
          {state?.data && (
            <div className="py-3 px-3 space-y-3 bg-fill/5">
              <RunDetailBody detail={state.data} compact />
              {state.data.children.length > 0 && (
                <div className="mt-3">
                  <div className="page-eyebrow mb-1">
                    Children ({state.data.children.length}) {state.data.children_truncated && <span className="ml-2 text-warning">已截断</span>}
                  </div>
                  <div className="border border-border rounded-md bg-surface">
                    {state.data.children.map(c => (
                      <RunNodeRow
                        key={c.id}
                        meta={c}
                        depth={depth + 1}
                        projectName={projectName}
                        isOpen={expanded.has(c.id)}
                        state={nodeCache[c.id]}
                        nodeCache={nodeCache}
                        expanded={expanded}
                        onToggle={onToggle}
                        onRetry={onRetry}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}, (prev, next) =>
  prev.meta === next.meta &&
  prev.depth === next.depth &&
  prev.projectName === next.projectName &&
  prev.isOpen === next.isOpen &&
  prev.state === next.state &&
  prev.onToggle === next.onToggle &&
  prev.onRetry === next.onRetry
  // Intentionally ignore nodeCache/expanded: they change reference whenever any
  // other node toggles, but this row only cares about its own state/isOpen.
  // Child rows receive fresh nodeCache/expanded, so they re-render correctly
  // when this row is open (parent branch re-renders) but siblings stay memo'd.
)

const RunDetailBody = memo(function RunDetailBody({ detail, compact }: { detail: RunDetail; compact?: boolean }) {
  const size = compact ? 'text-[11px]' : 'text-[12px]'
  return (
    <div className={`space-y-4 ${size}`}>
      <div className="grid grid-cols-2 gap-3">
        <PreviewField label="Name" value={detail.name} />
        <PreviewField label="Run Type" value={detail.run_type || '—'} />
        <PreviewField label="Status" value={detail.status} />
        <PreviewField label="ID" value={detail.id} mono />
        <PreviewField label="Latency" value={detail.latency_s != null ? `${detail.latency_s.toFixed(3)}s` : '—'} />
        <PreviewField label="Tokens" value={formatTokens(detail)} />
        <PreviewField label="Start" value={detail.start_time ? new Date(detail.start_time).toLocaleString() : '—'} />
        <PreviewField label="End" value={detail.end_time ? new Date(detail.end_time).toLocaleString() : '—'} />
      </div>
      {detail.tags.length > 0 && <PreviewField label="Tags" value={detail.tags.join(', ')} />}
      {detail.error && <PreviewField label="Error" value={detail.error} error />}
      <JsonField label="Inputs" value={detail.inputs} />
      <JsonField label="Outputs" value={detail.outputs} />
      {detail.metadata && <JsonField label="Metadata" value={detail.metadata} collapsed />}
      {detail.extra && <JsonField label="Extra" value={detail.extra} collapsed />}
    </div>
  )
})

function formatTokens(detail: RunDetail): string {
  const { prompt_tokens, completion_tokens, total_tokens } = detail
  if (total_tokens == null && prompt_tokens == null && completion_tokens == null) return '—'
  const parts: string[] = []
  if (prompt_tokens != null) parts.push(`prompt ${prompt_tokens}`)
  if (completion_tokens != null) parts.push(`completion ${completion_tokens}`)
  if (total_tokens != null) parts.push(`total ${total_tokens}`)
  return parts.join(' · ')
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      <div className="skeleton h-4 w-1/3 rounded" />
      <div className="skeleton h-16 w-full rounded" />
      <div className="skeleton h-4 w-1/4 rounded" />
      <div className="skeleton h-24 w-full rounded" />
    </div>
  )
}

function JsonField({ label, value, collapsed }: { label: string; value: unknown; collapsed?: boolean }) {
  const [open, setOpen] = useState(!collapsed)
  if (value == null || (typeof value === 'object' && value !== null && !Array.isArray(value) && Object.keys(value as object).length === 0)) {
    return <PreviewField label={label} value="—" />
  }
  const text = JSON.stringify(value, null, 2)
  return (
    <div>
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-1 page-eyebrow mb-1 hover:text-text-primary transition-colors"
      >
        <span>{open ? '▾' : '▸'}</span>
        <span>{label}</span>
        <span className="normal-case tracking-normal text-[10px] opacity-60">({text.length} chars)</span>
      </button>
      {open && (
        <pre className="text-[11px] leading-relaxed whitespace-pre-wrap break-all p-3 rounded-md border border-border bg-fill/5 text-text-primary font-mono max-h-80 overflow-y-auto">
          {text}
        </pre>
      )}
    </div>
  )
}

function PreviewField({ label, value, mono, error }: { label: string; value: string | null | undefined; mono?: boolean; error?: boolean }) {
  const text = value || '—'
  return (
    <div>
      <div className="page-eyebrow mb-1">{label}</div>
      <div className={`text-[12px] leading-relaxed whitespace-pre-wrap break-all p-3 rounded-md border ${
        error
          ? 'text-negative bg-negative/5 border-negative/20'
          : 'text-text-primary border-border bg-fill/5'
      } ${mono ? 'font-mono text-[11px]' : ''}`}>
        {text}
      </div>
    </div>
  )
}
