import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, useConfirm, useToast, ExportMenu } from '@/components/ui'
import {
  benchmarkApi,
  evaluationApi,
  projectsApi,
} from '@/services'
import { formatApiError, toToastMessage } from '@/lib/errors'
import type { ExportFormat } from '@/lib/download'
import type {
  BenchmarkCase,
  Project,
} from '@/services/benchmark'
import type {
  ConfigOption,
  EvalAgentConfig,
  EvalRunSummary,
  EvaluatorInstance,
  StartEvalRequest,
  UploadCasesResponse,
} from '@/types'
import { configOptionToString, useConfigOptions } from '@/hooks/useConfigOptions'

type Tab = 'history' | 'new'

export default function EvaluationPage() {
  const [tab, setTab] = useState<Tab>('history')

  return (
    <div>
      <header className="mb-6">
        <div className="page-eyebrow">评估</div>
        <h1 className="page-title">评估</h1>
        <p className="page-subtitle">运行管理 · LangSmith 追踪 · 本地评分</p>
      </header>

      <div className="page-tabs mb-5">
        {([
          { id: 'history', label: '运行历史' },
          { id: 'new', label: '新建评估' },
        ] as { id: Tab; label: string }[]).map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`page-tab ${tab === t.id ? 'page-tab-active' : ''}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'history' && <HistoryTab onNewRun={() => setTab('new')} />}
      {tab === 'new' && <NewRunTab onStarted={() => setTab('history')} />}
    </div>
  )
}


// ─── History tab ────────────────────────────────────────────────────────────

function HistoryTab({ onNewRun }: { onNewRun: () => void }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [searchText, setSearchText] = useState('')
  const [startedAfter, setStartedAfter] = useState('')   // YYYY-MM-DD
  const [startedBefore, setStartedBefore] = useState('') // YYYY-MM-DD
  const [minPassRate, setMinPassRate] = useState<string>('')  // percent string
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const pageSize = 15

  // Convert calendar values to ISO. Empty input → undefined (param dropped).
  const toIsoStart = (d: string) => d ? new Date(d + 'T00:00:00').toISOString() : undefined
  const toIsoEnd = (d: string) => d ? new Date(d + 'T23:59:59.999').toISOString() : undefined
  const passRateNum = (() => {
    const v = Number(minPassRate)
    if (!minPassRate || Number.isNaN(v)) return undefined
    return Math.max(0, Math.min(100, v)) / 100
  })()

  const runsQuery = useQuery({
    queryKey: ['eval-runs', page, statusFilter, searchText, startedAfter, startedBefore, minPassRate],
    queryFn: () =>
      evaluationApi.listRuns({
        page, page_size: pageSize,
        status: statusFilter || undefined,
        q: searchText.trim() || undefined,
        started_after: toIsoStart(startedAfter),
        started_before: toIsoEnd(startedBefore),
        min_pass_rate: passRateNum,
      }).then(r => r.data),
    refetchInterval: (q) => {
      const data = q.state.data
      if (!data) return false
      return data.items.some(it => it.status === 'running' || it.status === 'stopping') ? 3000 : false
    },
  })

  const totalPages = Math.max(1, Math.ceil((runsQuery.data?.total ?? 0) / pageSize))

  const toggle = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const clearFilters = () => {
    setStatusFilter('')
    setSearchText('')
    setStartedAfter('')
    setStartedBefore('')
    setMinPassRate('')
    setPage(1)
  }
  const filtersActive = !!(statusFilter || searchText || startedAfter || startedBefore || minPassRate)

  return (
    <div>
      <div className="toolbar">
        <select
          value={statusFilter}
          onChange={e => { setStatusFilter(e.target.value); setPage(1) }}
          className="select-sm"
        >
          <option value="">全部状态</option>
          <option value="running">运行中</option>
          <option value="completed">已完成</option>
          <option value="failed">失败</option>
          <option value="interrupted">已中断</option>
          <option value="stopping">停止中</option>
        </select>
        <input
          type="text" value={searchText}
          onChange={e => { setSearchText(e.target.value); setPage(1) }}
          placeholder="搜运行名 / 模型 / URL / 项目"
          className="input-sm w-[220px]"
        />
        <span className="text-[11px] text-text-tertiary">起</span>
        <input
          type="date" value={startedAfter}
          onChange={e => { setStartedAfter(e.target.value); setPage(1) }}
          className="input-sm"
        />
        <span className="text-[11px] text-text-tertiary">至</span>
        <input
          type="date" value={startedBefore}
          onChange={e => { setStartedBefore(e.target.value); setPage(1) }}
          className="input-sm"
        />
        <input
          type="number" min={0} max={100} step={5}
          value={minPassRate}
          onChange={e => { setMinPassRate(e.target.value); setPage(1) }}
          placeholder="通过率 ≥ %"
          className="input-sm w-[110px]"
        />
        {filtersActive && (
          <button
            onClick={clearFilters}
            className="text-[11px] text-text-tertiary hover:text-text-primary transition-colors"
          >
            清除筛选
          </button>
        )}
        <span className="text-[11px] text-text-tertiary tabular-nums">
          共 {runsQuery.data?.total ?? 0} 条
        </span>
        {selected.size > 0 && (
          <>
            <span className="text-[11px] text-text-tertiary">· 已选 {selected.size}</span>
            <Button
              variant="tinted"
              size="sm"
              disabled={selected.size < 2}
              onClick={() => navigate(`/evaluation/compare?ids=${Array.from(selected).join(',')}`)}
            >
              对比所选（{selected.size}）
            </Button>
            <ExportMenu
              label={`导出所选（${selected.size}）`}
              onExport={async (format: ExportFormat) => {
                try {
                  await evaluationApi.exportRunsSummary(Array.from(selected), format)
                } catch (e) {
                  toast.error(toToastMessage(formatApiError(e)), '导出失败')
                }
              }}
            />
            <button
              onClick={() => setSelected(new Set())}
              className="text-[11px] text-text-tertiary hover:text-text-primary transition-colors"
            >
              清空
            </button>
          </>
        )}
        <div className="flex-1" />
        <Button variant="primary" size="sm" onClick={onNewRun}>
          新建评估
        </Button>
      </div>

      <div className="table-card">
        <table className="table-base">
          <thead>
            <tr>
              <Th>
                <span className="sr-only">选择</span>
              </Th>
              <Th>ID</Th>
              <Th>状态</Th>
              <Th>智能体</Th>
              <Th>运行名</Th>
              <Th>进度 / 总数</Th>
              <Th>通过率</Th>
              <Th>平均时延</Th>
              <Th>启动时间</Th>
              <Th>操作</Th>
            </tr>
          </thead>
          <tbody>
            {runsQuery.isLoading && (
              <tr><td colSpan={10} className="empty-state">加载中…</td></tr>
            )}
            {runsQuery.data?.items.length === 0 && !runsQuery.isLoading && (
              <tr>
                <td colSpan={10} className="empty-state">
                  {filtersActive
                    ? '没有匹配筛选的评估记录'
                    : '还没有评估记录。点右上角「新建评估」启动第一个 run'}
                </td>
              </tr>
            )}
            {runsQuery.data?.items.map(r => (
              <RunRow
                key={r.id}
                run={r}
                selected={selected.has(r.id)}
                deleting={deletingId === r.id}
                onToggle={() => toggle(r.id)}
                onClick={() => navigate(`/evaluation/runs/${r.id}`)}
                onDelete={async () => {
                  const ok = await confirm({
                    title: '删除评估',
                    description: `确定删除评估 ${r.id.slice(0, 8)}？\n这是软删除，可在 DB 恢复 deleted_at 字段。`,
                    confirmText: '删除',
                    danger: true,
                  })
                  if (!ok) return
                  setDeletingId(r.id)
                  try {
                    await evaluationApi.deleteRun(r.id)
                    setSelected(prev => {
                      const next = new Set(prev)
                      next.delete(r.id)
                      return next
                    })
                    qc.invalidateQueries({ queryKey: ['eval-runs'] })
                    toast.success('评估已删除')
                  } catch (err) {
                    toast.error(extractError(err), '删除失败')
                  } finally {
                    setDeletingId(null)
                  }
                }}
              />
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2 mt-4 text-[12px] text-text-secondary">
          <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="pager-btn">
            ‹ 上一页
          </button>
          <span className="tabular-nums">{page} / {totalPages}</span>
          <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)} className="pager-btn">
            下一页 ›
          </button>
        </div>
      )}
    </div>
  )
}

function Th({ children }: { children: React.ReactNode }) {
  return <th>{children}</th>
}

function RunRow({ run, selected, deleting, onToggle, onClick, onDelete }: {
  run: EvalRunSummary
  selected: boolean
  deleting: boolean
  onToggle: () => void
  onClick: () => void
  onDelete: () => void
}) {
  const counts = run.summary_scores?.counts
  const total = counts?.total ?? run.progress.total ?? 0
  const completed = run.progress.completed ?? counts?.total ?? 0
  const passed = counts?.passed ?? 0
  const passRate = total > 0 ? `${Math.round((passed / total) * 100)}%` : '—'
  const avgLatency = firstDefined(
    run.summary_scores?.cost_success?.avg_latency_ms,
    run.summary_scores?.cost_failure?.avg_latency_ms,
  )

  const agent = run.agent_config as { model?: string; url?: string; type?: string }
  const agentLabel = agent?.model || agent?.type || '—'

  return (
    <tr
      onClick={onClick}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      role="button"
      tabIndex={0}
      aria-label={`查看运行 ${run.id.slice(0, 8)}`}
      className="cursor-pointer focus-visible:outline-none focus-visible:shadow-focus"
    >
      <Td>
        <input
          type="checkbox"
          checked={selected}
          onClick={e => e.stopPropagation()}
          onChange={onToggle}
          className="accent-accent w-3.5 h-3.5"
        />
      </Td>
      <Td mono>{run.id.slice(0, 8)}</Td>
      <Td><StatusBadge status={run.status} /></Td>
      <Td>{agentLabel}</Td>
      <Td mono>{run.langfuse_run_name ?? '—'}</Td>
      <Td>
        {run.status === 'running' ? `${completed}/${total || '?'}` : `${total}`}
      </Td>
      <Td>{passRate}</Td>
      <Td>{avgLatency != null ? `${Math.round(avgLatency)}ms` : '—'}</Td>
      <Td>{fmtTime(run.started_at)}</Td>
      <Td>
        <button
          onClick={e => { e.stopPropagation(); onDelete() }}
          disabled={deleting}
          className="text-action-danger disabled:opacity-50"
          title="软删除：行隐藏但 DB 保留 deleted_at"
        >
          {deleting ? '删除中…' : '删除'}
        </button>
      </Td>
    </tr>
  )
}

function Td({ children, mono }: { children: React.ReactNode; mono?: boolean }) {
  return (
    <td className={mono ? 'font-mono text-[11px]' : ''}>
      {children}
    </td>
  )
}

function StatusBadge({ status }: { status: string }) {
  const tone: Record<string, string> = {
    running: 'badge badge-info',
    completed: 'badge badge-positive',
    failed: 'badge badge-negative',
    stopping: 'badge badge-warning',
    interrupted: 'badge badge-neutral',
    pending: 'badge badge-neutral',
  }
  const cls = tone[status] ?? 'badge badge-neutral'
  return (
    <span className={cls}>
      {status === 'running' && (
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
      )}
      {status}
    </span>
  )
}


// ─── New run tab ────────────────────────────────────────────────────────────

type CaseSourceTab = 'benchmark' | 'upload'

function NewRunTab({ onStarted }: { onStarted: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()

  // Case-source tabs
  const [sourceTab, setSourceTab] = useState<CaseSourceTab>('benchmark')

  // ── benchmark branch ──
  const projectsQuery = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list().then(r => r.data),
  })
  const [projectId, setProjectId] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [searchText, setSearchText] = useState('')
  const [selectionMode, setSelectionMode] = useState<'all' | 'filter' | 'pick'>('all')
  const [pickedCaseIds, setPickedCaseIds] = useState<Set<string>>(new Set())
  const [filterTags, setFilterTags] = useState('')
  const [limit, setLimit] = useState<number | ''>(10)

  const categoriesQuery = useQuery({
    queryKey: ['categories', projectId],
    queryFn: () => projectsApi.getCategories(projectId).then(r => r.data),
    enabled: !!projectId,
  })
  const casesQuery = useQuery({
    queryKey: ['bench-cases-for-eval', projectId, categoryId, searchText],
    queryFn: () =>
      benchmarkApi.listCases(projectId, {
        category_id: categoryId || undefined,
        search: searchText || undefined,
        page: 1, page_size: 100,
      }).then(r => r.data),
    enabled: !!projectId && sourceTab === 'benchmark',
  })

  const effectiveCaseCount = useMemo(() => {
    if (!casesQuery.data) return 0
    if (selectionMode === 'pick') return pickedCaseIds.size
    return casesQuery.data.total
  }, [casesQuery.data, selectionMode, pickedCaseIds])

  // 真正会跑的样例数 = 命中数和 limit 取小（pick 模式不受 limit 影响）
  const willRunCount = useMemo(() => {
    if (selectionMode === 'pick') return pickedCaseIds.size
    if (typeof limit === 'number' && limit > 0) {
      return Math.min(effectiveCaseCount, limit)
    }
    return effectiveCaseCount
  }, [selectionMode, pickedCaseIds, effectiveCaseCount, limit])

  // ── upload branch ──
  const [uploadedSource, setUploadedSource] = useState<UploadCasesResponse | null>(null)
  const uploadMutation = useMutation({
    mutationFn: (file: File) => evaluationApi.uploadCases(file).then(r => r.data),
    onSuccess: (data) => setUploadedSource(data),
  })

  // ── agent ──
  const [agentType, setAgentType] = useState<'sse' | 'openai' | 'sse_generic'>('sse')
  const [agentUrl, setAgentUrl] = useState('')
  const [agentApiKey, setAgentApiKey] = useState('')
  const [agentModel, setAgentModel] = useState('')
  const [agentLanguage, setAgentLanguage] = useState('请用中文回复')
  const [agentHeadersText, setAgentHeadersText] = useState('')
  const [agentPayloadText, setAgentPayloadText] = useState('')
  const [agentTimeout, setAgentTimeout] = useState(300)
  const [concurrency, setConcurrency] = useState(3)
  const [runName, setRunName] = useState('')
  const [langsmithProject, setLangsmithProject] = useState('')

  // Multi-value config options — pickers in the form let users reuse
  // pre-saved presets from /config; the URL field also auto-prefills with
  // the default option on first load (falling back to a localhost hint
  // when no config is set).
  const endpointOpts = useConfigOptions('target_agent.endpoint_url')
  const apiKeyOpts = useConfigOptions('target_agent.api_key')
  const timeoutOpts = useConfigOptions('target_agent.timeout')
  const headersOpts = useConfigOptions('target_agent.headers')
  const payloadOpts = useConfigOptions('target_agent.request_template')
  const prefilledRef = useRef(false)
  useEffect(() => {
    if (prefilledRef.current) return
    if (endpointOpts.isLoading) return
    prefilledRef.current = true
    if (!agentUrl) {
      setAgentUrl(
        endpointOpts.defaultValue
          ? configOptionToString(endpointOpts.defaultValue)
          : 'http://localhost:18094/api/agent/langgraph',
      )
    }
  }, [endpointOpts.isLoading, endpointOpts.defaultValue, agentUrl])

  // ── evaluator instances ──
  const evaluatorsQuery = useQuery({
    queryKey: ['evaluator-instances-active'],
    queryFn: () => evaluationApi.listEvaluators(true).then(r => r.data),
  })
  const [selectedEvaluatorIds, setSelectedEvaluatorIds] = useState<Set<string>>(new Set())

  // ── start mutation ──
  const startMutation = useMutation({
    mutationFn: (body: StartEvalRequest) => evaluationApi.startRun(body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-runs'] })
      onStarted()
    },
  })

  const hasCaseSource = sourceTab === 'benchmark' ? !!projectId : !!uploadedSource
  const startBlockers: string[] = []
  if (!hasCaseSource) {
    startBlockers.push(sourceTab === 'benchmark' ? '选择基准项目' : '上传一个样例文件')
  }
  if (selectedEvaluatorIds.size === 0) startBlockers.push('勾选至少 1 个评估器')
  if (agentUrl.trim().length === 0) startBlockers.push('填写智能体 URL')
  const canStart = startBlockers.length === 0 && !startMutation.isPending

  const handleStart = () => {
    let headers: Record<string, string> | undefined
    let payloadTpl: Record<string, unknown> | undefined
    try {
      if (agentHeadersText.trim()) headers = JSON.parse(agentHeadersText)
    } catch { toast.error('请求头必须是合法 JSON'); return }
    try {
      if (agentPayloadText.trim()) payloadTpl = JSON.parse(agentPayloadText)
    } catch { toast.error('请求体模板必须是合法 JSON'); return }

    const agent: EvalAgentConfig = {
      type: agentType,
      url: agentUrl.trim(),
      api_key: agentApiKey || undefined,
      model: agentModel || undefined,
      headers,
      payload_template: payloadTpl,
      timeout: agentTimeout,
      language: agentLanguage,
    }

    const body: StartEvalRequest = {
      agent,
      evaluator_ids: Array.from(selectedEvaluatorIds),
      concurrency,
      run_name: runName.trim() || null,
      langsmith_project: langsmithProject.trim() || null,
    }

    if (sourceTab === 'upload' && uploadedSource) {
      body.case_source_id = uploadedSource.source_id
      body.limit = typeof limit === 'number' ? limit : null
    } else {
      body.project_id = projectId
      if (selectionMode === 'pick') {
        body.case_ids = Array.from(pickedCaseIds)
      } else if (selectionMode === 'filter') {
        body.filter_category_id = categoryId || null
        body.filter_tags = filterTags.split(',').map(t => t.trim()).filter(Boolean) || null
        body.limit = typeof limit === 'number' ? limit : null
      } else {
        body.filter_category_id = categoryId || null
        body.limit = typeof limit === 'number' ? limit : null
      }
    }

    startMutation.mutate(body)
  }

  return (
    <div className="flex flex-col gap-5 max-w-[900px]">
      {/* Step 1: case source */}
      <Section title="1. 选择样例来源">
        <div className="page-tabs mb-3">
          {([
            { id: 'benchmark', label: '从基准数据集' },
            { id: 'upload', label: '上传文件' },
          ] as { id: CaseSourceTab; label: string }[]).map(t => (
            <button
              key={t.id}
              onClick={() => setSourceTab(t.id)}
              className={`page-tab ${sourceTab === t.id ? 'page-tab-active' : ''}`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {sourceTab === 'benchmark' && (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="项目">
                <select
                  value={projectId}
                  onChange={e => {
                    setProjectId(e.target.value)
                    setCategoryId('')
                    setPickedCaseIds(new Set())
                  }}
                  className="input"
                >
                  <option value="">— 选择项目 —</option>
                  {projectsQuery.data?.map((p: Project) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </Field>
              <Field label="分类（可选）">
                <select
                  value={categoryId}
                  onChange={e => setCategoryId(e.target.value)}
                  disabled={!projectId}
                  className="input disabled:opacity-50"
                >
                  <option value="">全部分类</option>
                  {categoriesQuery.data?.map(c => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </Field>
            </div>

            <div className="flex items-center gap-3 mt-3 mb-2">
              {(['all', 'filter', 'pick'] as const).map(m => (
                <label key={m} className="inline-flex items-center gap-1.5 text-[12px] cursor-pointer">
                  <input type="radio" checked={selectionMode === m} onChange={() => setSelectionMode(m)} className="accent-accent" />
                  {m === 'all' && '全部'}
                  {m === 'filter' && '按条件筛选'}
                  {m === 'pick' && '手动勾选'}
                </label>
              ))}
            </div>

            {/* Selection summary banner */}
            {projectId && (
              <div className={`flex items-center gap-2 mb-3 px-3 py-2 rounded-md border text-[12px] ${
                willRunCount === 0
                  ? 'border-warning/30 bg-warning/10 text-warning'
                  : 'border-accent/30 bg-accent/5 text-text-primary'
              }`}>
                <span className="text-[14px]">{willRunCount === 0 ? '⚠' : '✓'}</span>
                <span>
                  本次将运行 <span className="font-mono font-medium">{willRunCount}</span> 条样例
                  {selectionMode === 'pick' && pickedCaseIds.size > 0 && (
                    <span className="text-text-tertiary ml-1.5">（手动勾选）</span>
                  )}
                  {selectionMode !== 'pick' && (
                    <>
                      <span className="text-text-tertiary ml-1.5">
                        （命中 {effectiveCaseCount} 条
                        {typeof limit === 'number' && limit > 0 && limit < effectiveCaseCount && `，受 limit ${limit} 限制`}
                        ）
                      </span>
                    </>
                  )}
                </span>
                {casesQuery.isLoading && <span className="text-text-tertiary ml-auto text-[11px]">载入中…</span>}
              </div>
            )}

            {selectionMode !== 'pick' && (
              <Field label="最多跑多少条（空=不限制）">
                <input
                  type="number" min={1} value={limit}
                  onChange={e => setLimit(e.target.value ? Number(e.target.value) : '')}
                  className="input max-w-[180px]"
                />
              </Field>
            )}
            {selectionMode === 'filter' && (
              <Field label="标签（逗号分隔）">
                <input
                  type="text" value={filterTags}
                  onChange={e => setFilterTags(e.target.value)}
                  placeholder="例如：电池,维修" className="input"
                />
              </Field>
            )}
            {selectionMode === 'pick' && (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <input
                    type="text" placeholder="搜索问题文本…"
                    value={searchText} onChange={e => setSearchText(e.target.value)}
                    className="input flex-1"
                  />
                  <Button variant="secondary" size="sm" onClick={() => setPickedCaseIds(new Set())}>
                    清空
                  </Button>
                </div>
                <div className="border border-border rounded-md max-h-[280px] overflow-y-auto bg-surface">
                  {casesQuery.data?.items.map((c: BenchmarkCase) => {
                    const checked = pickedCaseIds.has(c.id)
                    return (
                      <label key={c.id}
                             className="flex items-start gap-2 py-1.5 px-2.5 hover:bg-fill/5 cursor-pointer text-[12px] border-b border-separator last:border-b-0">
                        <input
                          type="checkbox" checked={checked}
                          onChange={() => {
                            const s = new Set(pickedCaseIds)
                            if (s.has(c.id)) s.delete(c.id); else s.add(c.id)
                            setPickedCaseIds(s)
                          }}
                          className="mt-0.5 accent-accent shrink-0"
                        />
                        <span className="flex-1 break-all">
                          {c.question.length > 120 ? c.question.slice(0, 120) + '…' : c.question}
                        </span>
                      </label>
                    )
                  })}
                  {casesQuery.data?.items.length === 0 && (
                    <div className="empty-state">
                      {projectId ? '没有匹配的样例' : '先选项目'}
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}

        {sourceTab === 'upload' && (
          <div>
            <div className="flex items-center gap-3">
              <input
                type="file" accept=".json,.jsonl"
                onChange={e => {
                  const f = e.target.files?.[0]
                  if (f) uploadMutation.mutate(f)
                }}
                className="text-[12px]"
              />
              {uploadMutation.isPending && <span className="text-[11px] text-text-tertiary">上传解析中…</span>}
              {uploadedSource && (
                <span className="text-[11px] text-positive">
                  已上传 {uploadedSource.count} 条：{uploadedSource.name}
                </span>
              )}
            </div>
            <p className="text-[10px] text-text-tertiary mt-2">
              支持 JSON（含 <code>test_cases</code> 数组或顶层数组）和 JSONL。每条必须有 <code>question</code> 字段。
              期望答案写在 <code>expected_output</code> 或 <code>reference_answer</code>。
            </p>
            {uploadedSource?.preview && uploadedSource.preview.length > 0 && (
              <div className="mt-3 border border-border rounded-md bg-fill/5 p-2.5">
                <div className="page-eyebrow mb-1">前 3 条预览</div>
                {uploadedSource.preview.map((c, i) => {
                  const q = String((c as { question?: unknown }).question ?? '')
                  return (
                    <div key={i} className="text-[11px] py-1 border-b border-separator last:border-b-0">
                      <span className="font-mono text-text-tertiary mr-2">
                        {String((c as { name?: unknown }).name ?? '')}
                      </span>
                      {q.length > 100 ? q.slice(0, 100) + '…' : q}
                    </div>
                  )
                })}
              </div>
            )}
            {uploadedSource && (
              <Field label="最多跑多少条（空=全部）">
                <input
                  type="number" min={1} value={limit}
                  onChange={e => setLimit(e.target.value ? Number(e.target.value) : '')}
                  className="input max-w-[180px] mt-3"
                />
              </Field>
            )}
          </div>
        )}
      </Section>

      {/* Step 2: agent */}
      <Section title="2. 配置智能体">
        <div className="grid grid-cols-2 gap-3">
          <Field label="类型">
            <select value={agentType} onChange={e => setAgentType(e.target.value as typeof agentType)} className="input">
              <option value="sse">SSE (LangGraph v2)</option>
              <option value="openai">OpenAI 兼容</option>
              <option value="sse_generic">SSE 通用模板</option>
            </select>
          </Field>
          <Field label="模型（可选，展示用）">
            <input type="text" value={agentModel} onChange={e => setAgentModel(e.target.value)} className="input" />
          </Field>
          <Field label="智能体 URL">
            <div className="relative">
              <input type="text" value={agentUrl} onChange={e => setAgentUrl(e.target.value)}
                     placeholder="http://localhost:18094/api/agent/langgraph" className="input pr-9" />
              <OptionPicker
                options={endpointOpts.options}
                currentValue={agentUrl}
                onPick={v => setAgentUrl(configOptionToString(v))}
              />
            </div>
          </Field>
          <Field label="API Key（可选）">
            <div className="relative">
              <input type="password" value={agentApiKey} onChange={e => setAgentApiKey(e.target.value)} className="input pr-9" />
              <OptionPicker
                options={apiKeyOpts.options}
                currentValue={agentApiKey}
                onPick={v => setAgentApiKey(configOptionToString(v))}
                maskValues
              />
            </div>
          </Field>
          <Field label="超时（秒）">
            <div className="relative">
              <input type="number" min={10} value={agentTimeout} onChange={e => setAgentTimeout(Number(e.target.value))} className="input pr-9" />
              <OptionPicker
                options={timeoutOpts.options}
                currentValue={String(agentTimeout)}
                onPick={v => {
                  const n = Number(configOptionToString(v))
                  if (!Number.isNaN(n) && n > 0) setAgentTimeout(n)
                }}
              />
            </div>
          </Field>
          <Field label="并发数">
            <input type="number" min={1} max={20} value={concurrency} onChange={e => setConcurrency(Number(e.target.value))} className="input" />
          </Field>
          {agentType === 'sse' && (
            <Field label="language 参数">
              <input type="text" value={agentLanguage} onChange={e => setAgentLanguage(e.target.value)} className="input" />
            </Field>
          )}
          <Field label="LangSmith 项目（用于拉回 trace）">
            <input type="text" value={langsmithProject} onChange={e => setLangsmithProject(e.target.value)}
                   placeholder="例如：ep-agent / ruyi-agent" className="input" />
          </Field>
        </div>

        <details className="mt-3">
          <summary className="text-[11px] text-text-secondary cursor-pointer">高级：自定义 headers / payload</summary>
          <div className="grid grid-cols-2 gap-3 mt-2">
            <Field label="请求头 (JSON)">
              <div className="relative">
                <textarea value={agentHeadersText} onChange={e => setAgentHeadersText(e.target.value)}
                          rows={3} placeholder='{"X-Custom": "value"}' className="input pr-9 font-mono text-[11px]" />
                <OptionPicker
                  options={headersOpts.options}
                  currentValue={agentHeadersText}
                  onPick={v => setAgentHeadersText(configOptionToString(v))}
                />
              </div>
            </Field>
            <Field label="请求体模板 (JSON, SSE 通用专用)">
              <div className="relative">
                <textarea value={agentPayloadText} onChange={e => setAgentPayloadText(e.target.value)}
                          rows={3} placeholder='{"question": "{input}"}' className="input pr-9 font-mono text-[11px]" />
                <OptionPicker
                  options={payloadOpts.options}
                  currentValue={agentPayloadText}
                  onPick={v => setAgentPayloadText(configOptionToString(v))}
                />
              </div>
            </Field>
          </div>
        </details>
      </Section>

      {/* Step 3: evaluators */}
      <Section title="3. 评估器">
        {evaluatorsQuery.isLoading && (
          <p className="text-[12px] text-text-tertiary">加载评估器…</p>
        )}
        {!evaluatorsQuery.isLoading && (evaluatorsQuery.data?.length ?? 0) === 0 && (
          <p className="text-[12px] text-text-tertiary">
            还没有评估器实例。到左侧菜单「评估器」页面先建一个。
          </p>
        )}
        <div className="flex flex-col gap-2">
          {evaluatorsQuery.data?.map((e: EvaluatorInstance) => {
            const checked = selectedEvaluatorIds.has(e.id)
            return (
              <label key={e.id}
                     className={`flex items-start gap-2 border rounded-md p-3 cursor-pointer transition-colors ${
                       checked ? 'border-accent bg-accent/5' : 'border-border hover:border-border-strong'
                     }`}>
                <input
                  type="checkbox" checked={checked}
                  onChange={() => {
                    const s = new Set(selectedEvaluatorIds)
                    if (s.has(e.id)) s.delete(e.id); else s.add(e.id)
                    setSelectedEvaluatorIds(s)
                  }}
                  className="mt-0.5 accent-accent"
                />
                <div className="flex-1">
                  <div className="font-medium text-[12px] flex items-center gap-2">
                    {e.name}
                    <span className="badge badge-accent font-mono" title="写到 Langfuse trace 的 tag">
                      {e.tag || e.name}
                    </span>
                  </div>
                  <div className="text-[11px] text-text-tertiary mt-0.5">{e.description || '—'}</div>
                </div>
              </label>
            )
          })}
        </div>
      </Section>

      {/* Step 4: run name */}
      <Section title="4. 运行名（可选）">
        <input
          type="text" value={runName} onChange={e => setRunName(e.target.value)}
          placeholder="默认自动按时间戳生成" className="input max-w-[420px]"
        />
      </Section>

      {/* Start */}
      <div className="flex items-center gap-3 pt-3 border-t border-separator">
        <Button
          variant="primary"
          size="lg"
          disabled={!canStart}
          loading={startMutation.isPending}
          onClick={handleStart}
          title={
            startBlockers.length > 0
              ? `还差：${startBlockers.map((b, i) => `${i + 1}) ${b}`).join('  ')}`
              : '启动评估'
          }
        >
          启动评估
        </Button>
        {startBlockers.length > 0 && !startMutation.isPending && (
          <span className="text-[11px] text-text-tertiary">
            还差：{startBlockers.map((b, i) => (
              <span key={i} className="ml-2">
                <span className="inline-block min-w-[1em] text-center text-text-tertiary mr-0.5">{i + 1})</span>
                {b}
              </span>
            ))}
          </span>
        )}
        {startMutation.isError && (
          <span className="text-[11px] text-negative">
            启动失败：{extractError(startMutation.error)}
          </span>
        )}
      </div>
    </div>
  )
}


// ─── Small helpers ──────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card p-4">
      <h3 className="text-subhead font-semibold mb-3 text-text-primary">{title}</h3>
      {children}
    </section>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-0">
      <span className="field-label">{label}</span>
      {children}
    </label>
  )
}

// Dropdown trigger that lists pre-configured options for a config key.
// Renders an absolutely-positioned chevron button — its parent must be
// `relative`, and the sibling input should reserve space with `pr-9`.
// Returns null when no options exist so unconfigured keys stay invisible.
//
// Fluent-Design notes: chevron rotates on open (motion), the popover floats
// with layered shadow + faint border (depth), the focused row has a 2px
// accent leading bar (light/selection), and ↑↓ Enter Esc work for keyboard
// users.
function OptionPicker({ options, currentValue, onPick, maskValues }: {
  options: ConfigOption[]
  currentValue: string
  onPick: (value: unknown) => void
  maskValues?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [focusIdx, setFocusIdx] = useState<number>(-1)
  const wrapRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const reactId = useId()
  const listboxId = `optionpicker-list-${reactId}`
  const optionId = (i: number) => `${listboxId}-opt-${i}`

  // When opening, focus the currently-selected option (or first).
  useEffect(() => {
    if (!open) { setFocusIdx(-1); return }
    const sel = options.findIndex(o => configOptionToString(o.value) === currentValue)
    setFocusIdx(sel >= 0 ? sel : 0)
  }, [open, options, currentValue])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); return }
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setFocusIdx(i => (i + 1) % options.length)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setFocusIdx(i => (i <= 0 ? options.length - 1 : i - 1))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        const opt = options[focusIdx]
        if (opt) { onPick(opt.value); setOpen(false) }
      } else if (e.key === 'Home') {
        e.preventDefault(); setFocusIdx(0)
      } else if (e.key === 'End') {
        e.preventDefault(); setFocusIdx(options.length - 1)
      }
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, options, focusIdx, onPick])

  // Keep the focused row in view.
  useEffect(() => {
    if (!open || focusIdx < 0) return
    const node = listRef.current?.querySelector<HTMLButtonElement>(`[data-idx="${focusIdx}"]`)
    node?.scrollIntoView({ block: 'nearest' })
  }, [open, focusIdx])

  if (!options || options.length === 0) return null

  const display = (v: unknown) => {
    const s = configOptionToString(v)
    if (maskValues && s) return s.length <= 6 ? '••••' : `••••${s.slice(-4)}`
    return s.length > 60 ? s.slice(0, 60) + '…' : s
  }

  return (
    <div ref={wrapRef} className="absolute right-1 top-1.5">
      <button
        type="button"
        tabIndex={-1}
        onClick={() => setOpen(o => !o)}
        aria-label="选择预设值"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={open ? listboxId : undefined}
        title="选择预设值"
        className={`inline-flex items-center justify-center w-6 h-6 rounded-md border transition-[color,background-color,border-color,box-shadow] duration-150 ease-standard ${
          open
            ? 'border-accent text-accent bg-accent/10 shadow-sm'
            : 'border-transparent text-text-tertiary hover:border-border hover:bg-fill/10 hover:text-text-primary'
        }`}
      >
        <svg
          viewBox="0 0 12 12"
          width="10"
          height="10"
          aria-hidden="true"
          className={`transition-transform duration-200 ease-standard ${open ? 'rotate-180' : ''}`}
        >
          <path d="M2.5 4.5l3.5 3.5 3.5-3.5" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div
          id={listboxId}
          role="listbox"
          aria-label="预设值"
          aria-activedescendant={focusIdx >= 0 ? optionId(focusIdx) : undefined}
          className="absolute right-0 top-[calc(100%+4px)] z-20 min-w-[260px] max-w-[380px] bg-bg-elevated border border-border rounded-lg shadow-lg overflow-hidden animate-popover-in origin-top-right"
        >
          <div className="px-3 py-1.5 flex items-center justify-between border-b border-separator bg-fill/5">
            <span className="text-[10px] tracking-[0.12em] uppercase text-text-tertiary font-medium">预设值</span>
            <span className="text-[10px] text-text-tertiary tabular-nums">{options.length}</span>
          </div>
          <div ref={listRef} className="max-h-64 overflow-auto py-1">
            {options.map((opt, i) => {
              const valueStr = configOptionToString(opt.value)
              const active = valueStr === currentValue
              const focused = i === focusIdx
              return (
                <button
                  key={i}
                  id={optionId(i)}
                  type="button"
                  data-idx={i}
                  role="option"
                  aria-selected={active}
                  onMouseEnter={() => setFocusIdx(i)}
                  onClick={() => { onPick(opt.value); setOpen(false) }}
                  title={maskValues ? opt.label || `选项 #${i}` : valueStr}
                  className={`relative w-full text-left pl-6 pr-3 py-1.5 text-[11px] flex flex-col gap-0.5 transition-colors duration-150 ease-standard ${
                    focused ? 'bg-fill/10' : ''
                  } ${active ? 'text-text-primary' : 'text-text-secondary'}`}
                >
                  {active && (
                    <span className="absolute left-1 top-1 bottom-1 w-[2px] rounded-full bg-accent" aria-hidden="true" />
                  )}
                  {active && (
                    <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 text-accent">
                      <path d="M2.5 6.2l2.4 2.4L9.5 3.6" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                  <span className="truncate font-medium">{opt.label ? opt.label : display(opt.value)}</span>
                  {opt.label && (
                    <span className="text-text-tertiary text-[10px] truncate font-mono">{display(opt.value)}</span>
                  )}
                </button>
              )
            })}
          </div>
          <div className="px-3 py-1 border-t border-separator bg-fill/5 text-[10px] text-text-tertiary flex items-center gap-2">
            <kbd className="font-mono px-1 py-px rounded border border-border bg-surface">↑↓</kbd>
            <span>导航</span>
            <kbd className="font-mono px-1 py-px rounded border border-border bg-surface">Enter</kbd>
            <span>选择</span>
            <kbd className="font-mono px-1 py-px rounded border border-border bg-surface">Esc</kbd>
            <span>关闭</span>
          </div>
        </div>
      )}
    </div>
  )
}

function firstDefined<T>(...vals: (T | null | undefined)[]): T | null {
  for (const v of vals) {
    if (v !== null && v !== undefined) return v
  }
  return null
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

function extractError(err: unknown): string {
  return toToastMessage(formatApiError(err, { fallbackMessage: '未知错误' }))
}
